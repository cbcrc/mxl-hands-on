// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#include <cstdio>
#include <mxl/time.h>
#include <mxl/flow.h>
#include <mxl/mxl.h>
#include <mxl/dataformat.h>
#include <cstdlib>
#include <chrono>

#include "engine.hpp"
#include "calls.hpp"

namespace
{
    // the tail kept in memory for the UI. The file (12d-2) holds everything. Eight lanes
    // at ~30 steps/s is ~500 event/s, so this is roughly the last ten seconds.
    constexpr std::size_t kMaxTail = 5000;
}
EventLog::EventLog()
{
    char const* const path = std::getenv("ABI_TESTER_LOG_FILE");
    if (path == nullptr)
    {
        std::printf("log file: (none, set ABI_TESTER_LOG_FILE)\n");
        return;
    }

    _path = path;
    if (!openRun())
    {
        return;     // warned already; the tail in memory still works
    }

    // Only started when there is a file; with none, nothing about the old workflow moves.
    _flusher = std::thread([this] { flushLoop(); });
}

// Run 1 is the path as given; run 2 is "log.2.ndjson", not "log.ndjson.2". The counter
// goes *before* the extension so read_json_auto('*.ndjson') still finds every run --
// which is the reason (d) chose NDJSON in the first place.
bool EventLog::openRun()
{
    std::filesystem::path name(_path);
    if (_run > 1)
    {
        name.replace_filename(name.stem().string() + "." + std::to_string(_run) +
                              name.extension().string());
    }

    // app, not the default truncate: two runs appending is recoverable, a run that
    // silently erased the previous one is not.
    _file.open(name, std::ios::out | std::ios::app);
    _fileOpen = _file.is_open();

    if (!_fileOpen)
    {
        std::fprintf(stderr, "cannot open %s; logging to memory only\n", name.c_str());
        return false;
    }

    std::printf("Log file: %s\n", name.c_str());
    return true;
}

uint64_t EventLog::append(std::string const& lane, std::string const& stepId,
                          uint64_t wallNs, nlohmann::json const& result)
{
    std::lock_guard<std::mutex> guard(_mutex);

    uint64_t const seq = ++_lastSeq;    // was _event.size() + 1, which trimming breaks

    nlohmann::ordered_json event;
    event["seq"]        = seq;
    event["lane"]       = lane;
    event["step_id"]    = stepId;
    event["t_wall_ns"] = std::to_string(wallNs);    // ns as a string: the M9 number contract
    event.update(result);                           // the adapter's own keys, flat

    _events.push_back(std::move(event));
    trim();
    return seq;
}

bool EventLog::since(uint64_t sinceSeq, nlohmann::ordered_json& out, std::string& error) const
{
    std::lock_guard<std::mutex> guard(_mutex);

    // sinceSeq is exclusive, so the oldest answerable question is _baseSeq - 1.
    if ((sinceSeq + 1) < _baseSeq)
    {
        error = "events up to seq " + std::to_string(_baseSeq - 1) +
                " are no longer in memory; ask for since=" + std::to_string(_baseSeq - 1) +
                " or later";
        return false;
    }

    out = nlohmann::ordered_json::array();

    for (std::size_t i = (std::size_t)(sinceSeq + 1 - _baseSeq); i < _events.size(); ++i)
    {
        out.push_back(_events[i]);
    }

    return true;
}

void EventLog::trim()
{
    // The tail bound yields to the file. An event the flusher has not written yet must
    // stay reachable, or the next drain skips it and the NDJSON has a hole no reader
    // can see. If the flusher falls behind, the deque grows pas kMaxTail -- memory is
    // what we are willing to spend to not loose a measurement.
    // With no file, nothing is waiting, so the bound is unconditional.
    uint64_t const floor = _fileOpen ? _flushedSeq : _lastSeq;

    while (_events.size() > kMaxTail && _baseSeq <= floor)
    {
        _events.pop_front();
        ++_baseSeq;
    }
}

void EventLog::drainLocked(std::unique_lock<std::mutex>& lock)
{
    if (!_fileOpen || _flushedSeq >= _lastSeq)
    {
        return;
    }

    // Snapshot the high-water mark *before* unlocking: lanes keep appending while we
    // write, and _flushedSeq must name what went out, not what exists now.
    uint64_t const    upTo = _lastSeq;
    std::size_t const from = (std::size_t)(_flushedSeq + 1 - _baseSeq);

    // One bulk copy under the lock, then N writes without it -- the same
    // claim-under-lock / work-without-it shape as stepOnce and Registry::take.
    std::vector<nlohmann::ordered_json> batch(_events.begin() + from, _events.end());

    lock.unlock();

    for (auto const& event : batch)
    {
        _file << event.dump() << "\n";      // compact: no newline inside, NDJSON's one rule
    }
    _file.flush();      // once per batch, not once per event -- the point of 12d-3

    lock.lock();
    _flushedSeq = upTo;
}

void EventLog::flushLoop()
{
    for (;;)
    {
        std::unique_lock<std::mutex> lock(_mutex);

        // wait_for, not wait: the timeout *is* the schedule, and the predicate only
        // lets shutdown and rotation cut the 2 s short. The return value says which
        // of the two happened, and neither branch needs to know.
        _flushWake.wait_for(lock, std::chrono::seconds(2),
                            [this] {return _shutdown || _rotate; });
        
        bool const stopping = _shutdown;

        drainLocked(lock);      // returns holding the lock

        if (_rotate)
        {
            // Ordering matters: the old run's tail is on disk before its file close.
            _file.close();
            ++_run;
            openRun();
            _rotate = false;
            _flushWake.notify_all();    // wakes clear(), waiting on !_rotate
        }

        if (stopping)
        {
            return;         // the last act was drain, so the file is complete
        }
    }
}

void EventLog::clear()
{
    std::unique_lock<std::mutex> lock(_mutex);

    // reset() has already waited _busy down to zero, so no lane is appending here --
    // which is what makes "drain, then rotate, then zero the counters" a coherent order.
    if (_flusher.joinable())
    {
        // Only the flusher ever touches the file, so ask it to rotate and wait rather
        // than closing the stream under it.
        _rotate = true;
        _flushWake.notify_all();
        _flushWake.wait(lock, [this] { return !_rotate; });
    }

    _events.clear();
    _baseSeq    = 1;
    _lastSeq    = 0;
    _flushedSeq = 0;
    
}

EventLog::~EventLog()
{
    if (!_flusher.joinable())
    {
        return;
    }

    {
        std::lock_guard<std::mutex> guard(_mutex);
        _shutdown = true;
    }
    _flushWake.notify_all();
    _flusher.join();
    
}

namespace
{
    // A..Z, then AA, AB, ... -- bijective base 26. Note there is no zero digit:
    // "A" is 0 *and* 1 depending of position, which is why the loop substract one
    // before dividing. Plain base 26 would emit "@A" for 26.
    std::string laneName(int ordinal)
    {
        std::string name;
        for (int n = ordinal; n >= 0; n = n / 26 - 1)
        {
            name.insert(name.begin(), (char)('A' + (n % 26)));
        }
        return name;
    }

    // Pool size from the environment. One OS thread per lane, and an idle lane
    // wakes every 5 ms, so the ceiling is about the instrument's own load -- not
    // about how many names we can spell.
    int laneCount()
    {
        char const* fromEnv = std::getenv("ABI_TESTER_LANES");
        if (fromEnv == nullptr)
        {
            return 8;
        }

        char*      end = nullptr;
        long const n   = std::strtol(fromEnv, &end, 10);
        
        if ((end == fromEnv) || (*end != '\0') || (n < 1) || (n > 1024))
        {
            std::fprintf(stderr, "ABI_TESTER_LANES=%s ignored (want 1..1024); using 8\n", fromEnv);
            return 8;
        }
        return (int)n;
    }
}

Engine::Engine(Registry& registry, EventLog& log)
    : _registry(registry)
    , _log(log)
{
    int const count = laneCount();

    for (int i = 0; i < count; ++i)
    {
        std::string const name = laneName(i);
        _lanes[name].name      = name;
    }

    // Second loop, not merged into the first: a thread calls laneFor, which reads
    // _lanes without the lock. Nothing may still be inserting when that starts.
    for (auto& entry: _lanes)
    {
        Lane* lane = &entry.second;     // not a structured binding: a lambda cannot capture one
        _threads.emplace_back([this, lane] { laneLoop(*lane); });
    }

    std::printf("Lanes: %d (%s..%s)\n", count,
                laneName(0).c_str(), laneName(count - 1).c_str());
}

Engine::~Engine()
{
    {
        std::lock_guard<std::mutex> guard(_mutex);
        _shutdown = true;
    }
    _wake.notify_all();

    for (std::thread& thread : _threads)
    {
        if (thread.joinable())
        {
            thread.join();
        }
    }
}

namespace
{   
    // One index expression -> one number. This is the only place the four modes
    // are spelled out.
    bool resolveIndex(Registry const& registry, nlohmann::json const& expr,
                      nlohmann::json const& args, uint64_t cursor,
                      uint64_t&out, nlohmann::json& resolved, std::string& error)
    {
        std::string const mode   = expr.value("mode", std::string{"literal"});
        int64_t const     offset = expr.value("offset", (int64_t)0);

        if (mode == "literal")
        {
            if (!expr.contains("value") || !expr["value"].is_number_unsigned())
            {
                error = "index mode \"literal\" needs an unsigned \"value\"";
                return false;
            }
            out = expr["value"].get<uint64_t>();
        }
        else if (mode == "cursor")
        {
            out = cursor;
        }
        else if (mode == "current")
        {
            // The expression wins; the step's own edit_rate is the fallback, so the
            // four time.h calls do not have to spell the same rational twice.
            mxlRational editRate{};
            if (!argRational(expr, "edit_rate", editRate) &&
                !argRational(args, "edit_rate", editRate))
            {
                error = "index mode \"current\" needs an "
                        "\"edit_rate\" {\"num\":<int>,\"den\":<non-zero int>}";
                return false;
            }

            out = mxlGetCurrentIndex(&editRate);
        }
        else if (mode == "head")
        {
            // Same fallback rule as edit_rate: the expression wins, the step's own
            // argument fills in -- a read step already names its reader.
            std::string readerName = expr.value("reader", std::string{});
            if (readerName.empty())
            {
                readerName = args.value("reader", std::string{});
            }

            auto const reader = static_cast<mxlFlowReader>(
                registry.find(readerName, HandleKind::FlowReader));
            if (reader == nullptr)
            {
                error = "index mode \"head\" needs a flow reader handle in \"reader\"";
                return false;
            }

            // One call, not two: GetInfo returns config and runtime together, so the
            // format branch and the head index cost a single trip through the ABI.
            mxlFlowInfo     info{};
            mxlStatus const status = mxlFlowReaderGetInfo(reader, &info);
            if (status != MXL_STATUS_OK)
            {
                error = "index mode \"head\": mxlFlowReaderGetInfo returned status " +
                        std::to_string(status);
                return false;
            }

            bool const     discrete = mxlIsDiscreteDataFormat(info.config.common.format) != 0;
            uint64_t const head     = info.runtime.headIndex;

            if (!discrete && (head == 0))
            {
                // 0 - 1 in uint64_t is 1.8e19: the M9 argUint64 lesson, arriving at the
                // ABI as perfectly plausible-looking index.
                error = "index mode \"head\": flow has no committed data yet";
                return false;
            }

            // Discrete headIndex is the last committed grain, inclusive; continuous is
            // the exclusive end of the last committed range. Normalising both to "the
            // newest readable index" is what makes one expression mean one thing.
            out = discrete ? head : (head - 1);

            resolved["head"] = head;
            resolved["flow"] = discrete ? "discrete" : "continuous";
        }
        else
        {
            error = "unknown index mode: " + mode;
            return false;
        }

        out = (uint64_t)((int64_t)out + offset);    // offset is signed: head-1 is a real case
        return true;
    }

    // Rewrite every objet-valued argument carrying a "mode" into the index it
    // resolves to. `resolved` collects what was substituted, for the event log.
    bool resolveArgs(Registry const& registry, nlohmann::json& args, uint64_t cursor,
                     nlohmann::json& resolved, std::string& error)
    {
        for (auto& [key, value] : args.items())
        {
            if (!value.is_object() || !value.contains("mode"))
            {
                continue;       // edit_rate's {num,den}, and every plain scalar
            }

            uint64_t index = 0;
            if (!resolveIndex(registry, value, args, cursor, index, resolved,  error))
            {
                error = key + ": " + error;
                return false;
            }

            value         = index;    // the adapter sees a number, as it always has
            resolved[key] = index;
        }
        return true;
    }

    // Same number as calls.cpp's kMaxSleepNs, a different reason: there it is an HTTP
    // connection that must not look hung, here it is a lane thread holding _busy across
    // execute(), which /reset waits out. A pace ten minutes ahead would freeze the transport.
    constexpr uint64_t kMaxPaceNs = 5'000'000'000ULL;

    // The rate a paced step aims at: the cached grain rate of whichever handle the step
    // names. Cached since chunk 11e precisely so pacing costs no ABI call inside the
    // instrument's own timing path. Writer first -- a step naming both is writing.
    bool paceRate(Registry const& registry, nlohmann::json const& args, mxlRational& out)
    {
        auto const writer = args.find("writer");
        if ((writer != args.end()) && writer->is_string())
        {
            return registry.grainRate(writer->get<std::string>(), HandleKind::FlowWriter, out);
        }

        auto const reader = args.find("reader");
        if ((reader !=args.end()) && reader->is_string())
        {
            return registry.grainRate(reader->get<std::string>(),HandleKind::FlowReader, out);
        }

        return false;
    }

    // One lane's step array -> a vector<Step>. Fails on the first step that names a
    // call the catalog does not have -- validating at load time is the whole point.
    bool parseLane(nlohmann::json const& array, char const* laneName,
                   std::vector<Step>& out, std::string& error)
    {
        if (!array.is_array())
        {
            error = std::string("lane ") + laneName + ": must be an array of steps";
            return false;
        }

        for (auto const& item : array)
        {
            if (!item.is_object() || !item.contains("call") || !item["call"].is_string())
            {
                error = std::string("lane ") + laneName + ": every step needs a string \"call\"";
                return false; 
            }

            Step step;
            step.call = item["call"].get<std::string>();
            
            // setCursor is lane bookkeeping, not an ABI call. It is deliberately absent
            // from the catalog so /abi-calls keeps listing exactly 42 and stays diffable
            // against nm on the shared object.
            if ((step.call != "setCursor") && (step.call != "repeat") &&
                (findCall(step.call) == nullptr))
            {
                error = std::string("lane ") + laneName + ": unknown call: " + step.call;
                return false;
            }

            step.id             = item.value("id", std::string{});
            step.delayBeforeMs  = item.value("delay_before_ms", 0.0);
            step.advanceCursor  = item.value("advance_cursor", (int64_t)0);

            // The id if the step has one, else the call name: an error that does not say
            // *which* step is a grep through the scenario file.
            std::string const where = std::string("lane ") + laneName + " step " +
                                      (step.id.empty() ? step.call : step.id);
            
            if (item.contains("pace"))
            {
                if (!item["pace"].is_object())
                {
                    error = where + ": \"pace\" must be an object";
                    return false;
                }

                if (item.contains("delay_before_ms"))
                {
                    error = where + ": \"pace\" and \"delay_before_ms\" are mutually exclusive";
                    return false;
                }

                nlohmann::json const& pace = item["pace"];

                // Checked before value(): unlike Python's dict.get, nlohmann's value()
                // *throws* when the key is present with the wrong type, and nothing here
                // catches it -- a typo in a scenario file would kill the request thread.
                if (pace.contains("offset_ms") && !pace["offset_ms"].is_number())
                {
                    error = where + ": \"pace.offset_ms\" must be a number";
                    return false;
                }

                step.paced        = true;
                step.paceOffsetMs = pace.value("offset_ms", 0.0);
            }

            if (item.contains("fill") && item["fill"].is_object())
            {
                step.fill = item["fill"];
            }

            // "out": {"grain": "g"} is the spec's spelling for naming a step's product.
            // The key names the kind and is documentation only; the value is the
            // registry name, which is what every creating adapter reads as "store_as".
            if (item.contains("out"))
            {
                if (!item["out"].is_object() || (item["out"].size() != 1) ||
                    !item["out"].begin().value().is_string())
                {
                    error = std::string("lane ") + laneName +
                            ": \"out\" must be an object with exactly one string value";
                    return false;
                }

                CallSpec const* spec   = findCall(step.call);
                bool            stores = false;
                if (spec != nullptr)
                {
                    for (ParamSpec const& param : spec->params)
                    {
                        if (param.name == "store_as")
                        {
                            stores = true;
                            break;
                        }
                    }
                }

                if (!stores)
                {
                    error = std::string("lane ") + laneName + ": " + step.call +
                    " stores no handle, so it takes no \"out\"";
                    return false;
                }

                step.out = item["out"].begin().value().get<std::string>();
            }

            if (item.contains("args") && item["args"].is_object())
            {
                step.args = item["args"];
            }

            if (step.call == "repeat")
            {
                std::string const target = step.args.value("to", std::string{});
                if (target.empty())
                {
                    error = where + ": repeat needs \"to\" naming an earlier step's id";
                    return false;
                }

                // Backwards over the steps parsed so far. std::size_t is unsigned, so
                // `i >=0` would be forever true -- count down to 1 and index i - 1.
                bool found = false;
                for (std::size_t i = out.size(); i > 0; --i)
                {
                    if (out[i - 1].id == target)
                    {
                        step.repeatTo = i - 1;
                        found         = true;
                        break;
                    }
                }

                if (!found)
                {
                    error = where + ": repeat target \"" + target +
                            "\" is not an earlier step in this lane";
                    return false;
                }

                if (!step.args.contains("times") || !step.args["times"].is_number_unsigned())
                {
                    error = where + ": repeat needs an unsigned \"times\"";
                    return false;
                }

                step.repeatTimes = step.args["times"].get<uint64_t>();
            }
            out.push_back(std::move(step));
        }
        return true;
    }

    void seedRepeats(Lane& lane)
    {
        lane.repeatsLeft.assign(lane.steps.size(), 0);
        for (std::size_t i = 0; i < lane.steps.size(); ++i)
        {
            lane.repeatsLeft[i] = lane.steps[i].repeatTimes;
        }
    }
}

bool Engine::loadScenario(nlohmann::json const& doc, std::string& error)
{
    if (!doc.is_object())
    {
        error = "scenario must be an object";
        return false;
    }

    nlohmann::json const lanes =
        doc.contains("lanes") ? doc["lanes"] : nlohmann::json::object();
    if (!lanes.is_object())
    {
        error = "\"lanes\" must be an object";
        return false;
    }

    // Parse into a locals map first: a lane that fails must not leave every loaded
    // lane exactly as it was.
    std::map<std::string, std::vector<Step>> parsed;

    for (auto const& item : lanes.items())
    {
        std::string const& name = item.key();

        // Reading the immutable pool without the lock, as laneFor does.
        if (_lanes.find(name)  == _lanes.end())
        {
            error = "unknown lane \"" + name + "\": the pool is " + laneName(0) + ".." +
                    laneName((int)_lanes.size() - 1) +
                    "; set ABI_TESTER_LANES to widen it";
            return false;
        }

        if (!parseLane(item.value(), name.c_str(), parsed[name], error))
        {
            return false;
        }
    }

    std::lock_guard<std::mutex> guard(_mutex);

    for (auto& entry : _lanes)
    {
        auto const found = parsed.find(entry.first);

        // A pool lane the document does not name is emptied, never left behind:
        // the previous scenario's steps must not survive into this one.
        entry.second.steps  = (found == parsed.end()) ? std::vector<Step>{}
                                                      : std::move(found->second);
        entry.second.next   = 0;
        entry.second.cursor = 0;
        seedRepeats(entry.second);
    }

    _running  = false;
    _scenario = doc;
    return true;
    
}

nlohmann::ordered_json Engine::state() const
{
    nlohmann::ordered_json lanes = nlohmann::ordered_json::object();
    {
        std::lock_guard<std::mutex> guard(_mutex);
        for (auto const& entry : _lanes)
        {
            Lane const& lane = entry.second;
            if (lane.steps.empty())
            {
                continue;   // a pool lane nobody loaded: not part of this scenario
            }

            nlohmann::ordered_json item;
            item["steps"]     = lane.steps.size();
            item["next"]      = lane.next;
            item["cursor"]    = lane.cursor;
            lanes[entry.first] = item;
        }
    }   // _mutex released here, deliberately: snapshot() takes the registry's own lock,
        // and holding two locks at once is how deadlocks get built.
    
    nlohmann::ordered_json handles = nlohmann::ordered_json::object();
    for (auto const& [name, entry] : _registry.snapshot())
    {
        char ptrText[32];
        std::snprintf(ptrText, sizeof(ptrText), "%p", entry.ptr);

        nlohmann::ordered_json item;
        item["kind"] = handleKindName(entry.kind);
        item["note"] = entry.note;
        item["ptr"]  = ptrText;

        if (entry.owner != nullptr)
        {
            char ownerText[32];
            std::snprintf(ownerText, sizeof(ownerText), "%p", entry.owner);
            item["owner"] = ownerText;
        }

        handles[name] = item;
    }

    nlohmann::ordered_json body;
    body["running"]     = _running.load();
    body["delay_scale"] = _delayScale.load();
    body["lane_pool"]   = _lanes.size();
    body["lanes"]       = lanes;
    body["handles"]     = handles;
    return body;
}

Lane* Engine::laneFor(std::string const& name)
{
    // No lock: the pool is complete before the first thread starts and is never
    // touched again, so this is a read of an immutable container.
    auto const it = _lanes.find(name);
    return (it == _lanes.end()) ? nullptr : &it->second;
}

bool Engine::stepOnce(std::string const& laneName, std::string& error)
{
    Lane* lane = laneFor(laneName);
    if (lane == nullptr)
    {
        error = "unknown lane: " + laneName;
        return false;
    }

    // Claim the step under the lock -- a copy, not a reference: a concurrent
    // /scenario would free the vector the reference points into.
    Step     step;
    uint64_t cursor = 0;
    {
        std::lock_guard<std::mutex> guard(_mutex);
        
        if (_resetting)
        {
            error = "reset in progress";
            return false;
        }

        if (lane->next >= lane->steps.size())
        {
            error = "lane " + laneName + " has no step left";
            return false;
        }

        step   = lane->steps[lane->next];
        cursor = lane->cursor;

        // Lane bookkeeping, done here rather than in execute(): a repeat runs no adapter,
        // so dropping the lock to "execute" it would only open a window in which next and
        // repeatsLeft disagree.
        if (step.call == "repeat")
        {
            uint64_t& left = lane->repeatsLeft[lane->next];
            if (left > 0)
            {
                --left;
                lane->next = step.repeatTo;     // backward by construction: parseLane refused forward
            }
            else
            {
                ++lane->next;                   // exhausted: fall through to whatever follows
            }

            return true;    // _busy untouched -- nothing is in flight for /reset to wait out
        }
        
        ++lane->next;   // claimed before it runs: a step is never executed twice
        ++_busy;        // inside the lock: the gap between claiming and marking busy
    }                   // is exactly the window reset would slip through

    uint64_t const nextCursor = execute(laneName, step, cursor);

    {
        std::lock_guard<std::mutex> guard(_mutex);
        lane->cursor = nextCursor;      // `lane` stays valid: it is a member address
    }

    --_busy;
    return true;
}

uint64_t Engine::execute(std::string const& laneName, Step const& step, uint64_t cursor)
{
    double const delayMs = step.delayBeforeMs * _delayScale.load();
    if (delayMs > 0.0)
    {
        mxlSleepForNs((uint64_t)(delayMs * 1000000.0));
    }

    // Resolve index expressions against *this* lane's cursor -- on a copy, so the
    // scenario itself is never rewritten and the same step re-runs identically
    // after a /reset.
    nlohmann::json args     = step.args;
    nlohmann::json resolved = nlohmann::json::object();
    std::string    error;

    if (!resolveArgs(_registry, args, cursor, resolved, error))
    {
        _log.append(laneName, step.id, mxlGetTime(),
                    nlohmann::json{{"ok", false}, {"call", step.call}, {"error", error}});
        return cursor;
    }

    // Absolute pacing: aim at the grain's own OTS, so a late step catches up instead of
    // pushing everything after it. delay_before_ms restarts its clock each step and
    // compounds -- chunk 5 measured 115-367 us of overshoot per step.
    nlohmann::json pace;    // plain json not ordered_json: result is plain json below
    if (step.paced)
    {
        mxlRational rate{};
        if (!paceRate(_registry, args, rate))
        {
            _log.append(laneName, step.id, mxlGetTime(),
                        nlohmann::json{{"ok", false}, {"call", step.call},
                                        {"error", "pace needs a \"writer\" or \"reader\" handle "
                                                   "with a known grain rate"}});
            return cursor;
        }

        if (!args.contains("index") || !args["index"].is_number_unsigned())
        {
            _log.append(laneName, step.id, mxlGetTime(),
                        nlohmann::json{{"ok", false}, {"call", step.call},
                                       {"error", "pace needs a resolved unsigned \"index\""}});
            return cursor;
        }

        uint64_t const ots     = mxlIndexToTimestamp(&rate, args["index"].get<uint64_t>());
        int64_t const offsetNs = (int64_t)(step.paceOffsetMs * 1000000.0);

        // Through int64_t deliberately: a negative offset on unsigned arithmetic wraps to
        // 1.8e19 and arrives at the ABI as a perfectly plausible timestamp -- the M9 lesson.
        uint64_t const deadline = (uint64_t)((int64_t)ots + offsetNs);

        // The cap protects the transport, not the sleep: this thread holds _busy across
        // execute(), and /reset spins until _busy is zero.
        uint64_t const now    = mxlGetTime();
        bool const     capped = (deadline > now) && ((deadline - now) > kMaxPaceNs);

        mxlSleepUntil(capped ? (now + kMaxPaceNs) : deadline);

        uint64_t const woke = mxlGetTime();
        pace["deadline_ns"] = std::to_string(deadline);     // ns as a string: the M9 contract
        pace["late_ms"]     = ((int64_t)woke -(int64_t)deadline) / 1000000.0;
        if (capped)
        {
            pace["capped"] = true;      // late_ms comes back negative: woke early, not lying
        }
    }

    // Merged AFTER resolution, never before: fill is an object carrying a "mode" key,
    // which is exactly the shape resolveArgs treats as an index expression. Keeping
    // fill a sibling of args in the scenario -- as the spec spells it -- is what saves
    // the structural rule from growing a special case.
    if (!step.fill.is_null())
    {
        args["fill"] = step.fill;
    }

    if (!step.out.empty())
    {
        args["store_as"] = step.out;
    }

    if (step.call == "setCursor")
    {
        if (!args.contains("index") || !args["index"].is_number_unsigned())
        {
            _log.append(laneName, step.id, mxlGetTime(),
                        nlohmann::json{{"ok", false},
                                       {"call", step.call},
                                       {"error", "setCursor needs an unsigned \"index\""}});
            return cursor;
        }

        uint64_t const seeded = args["index"].get<uint64_t>();

        nlohmann::json event{{"ok", true}, {"call", step.call},
                             {"abi", false}, {"cursor", seeded}};
        if (!resolved.empty())
        {
            event["resolved"] = resolved;
        }
        
        _log.append(laneName, step.id, mxlGetTime(), event);

        // advance_cursor is ignored on purpose: setCursor names an absolute index, and
        // stacking a relative advance on the seed would make it unreadable in the log.
        return seeded;
    }

    CallSpec const* spec = findCall(step.call);
    if (spec == nullptr)
    {
        // Cannot happen: parseLane refused unknown names. Logged, not asserted.
        _log.append(laneName, step.id, mxlGetTime(),
                    nlohmann::json{{"ok", false},
                                   {"call", step.call},
                                   {"error", "unknown call"}});
        return cursor;
    }

    uint64_t const startNs = mxlGetTime();
    nlohmann::json result  = spec->invoke(_registry, args);
    uint64_t const endNs   = mxlGetTime();

    if (!result.is_object())
    {
        result = nlohmann::json{{"ok", false}, {"error", "adapter did not return a JSON object"}};
    }

    result["call"]        = step.call;
    result["duration_us"] = (endNs - startNs) / 1000.0;

    if (!pace.is_null())
    {
        result["pace"] = pace;
    }

    if (!resolved.empty())
    {
        result["resolved"] = resolved;
    }

    _log.append(laneName, step.id, endNs, result);
    return (uint64_t)((int64_t)cursor + step.advanceCursor);
}

void Engine::laneLoop(Lane& lane)
{
    for (;;)
    {
        {
            std::unique_lock<std::mutex> lock(_mutex);

            _wake.wait(lock,
                       [this, &lane]
                       {
                            return _shutdown || (_running && !_resetting &&
                                                 (lane.next < lane.steps.size()));
                        });
            
            if (_shutdown)
            {
                return;
            }
        }   // the lock is released here, deliberately: stepOnce takes _mutex itself,
            // and this mutex is not recursive (the session-5 rule).
        
        std::string error;
        stepOnce(lane.name, error);     // may find the state changed and refuse; we just re-wait
    }
}

void Engine::run()
{
    {
        std::lock_guard<std::mutex> guard(_mutex);
        _running = true;    // under the lock even though it is atomic: see laneLoop
    }
    _wake.notify_all();
}

void Engine::pause()
{
    _running = false;   // the current step, delay included, still finishes
}

void Engine::setDelayScale(double scale)
{
    if (scale > 0.0)
    {
        _delayScale = scale;
    }
}

nlohmann::ordered_json Engine::reset()
{
    pause();
    _resetting = true;

    // pause() only stops the *next* step. Wait out the in-flight one: it may be
    // seconds inside an adapter, holding a handle we are about to release.
    for (;;)
    {
        {
            std::lock_guard<std::mutex> guard(_mutex);
            if (_busy == 0)
            {
                for (auto& entry : _lanes)
                {
                    entry.second.next   = 0;
                    entry.second.cursor = 0;
                    seedRepeats(entry.second);
                }
                break;
            }
        }
        mxlSleepForNs(5000000);
    }

    std::map<std::string, HandleEntry> const handles = _registry.drain();

    // Fixed order by kind, not reverse creation order: a writer or reader must be
    // gone before its instance, and a sync group holds raw reader pointers.
    static HandleKind const kOrder[] = {HandleKind::Grain, HandleKind::SyncGroup,
                                        HandleKind::FlowWriter, HandleKind::FlowReader,
                                        HandleKind::Instance};
    
    nlohmann::ordered_json released = nlohmann::ordered_json::array();

    for (HandleKind const kind : kOrder)
    {
        for (auto const& [name, entry] : handles)
        {
            if (entry.kind != kind)
            {
                continue;
            }

            auto const owner = static_cast<mxlInstance>(entry.owner);

            switch (kind)
            {
                case HandleKind::Grain:
                    break;      // owns nothing; the open index lives in the writer
                case HandleKind::SyncGroup:
                    mxlReleaseFlowSynchronizationGroup(
                        owner, static_cast<mxlFlowSynchronizationGroup>(entry.ptr));
                    break;
                case HandleKind::FlowWriter:
                    mxlReleaseFlowWriter(owner, static_cast<mxlFlowWriter>(entry.ptr));
                    break;
                case HandleKind::FlowReader:
                    mxlReleaseFlowReader(owner, static_cast<mxlFlowReader>(entry.ptr));
                    break;
                case HandleKind::Instance:
                    mxlDestroyInstance(static_cast<mxlInstance>(entry.ptr));
                    break;
            }

            released.push_back(name);
        }
    }

    _log.clear();
    _resetting = false;

    nlohmann::ordered_json body = state();
    body["released"] = released;
    return body;
}

nlohmann::json Engine::scenario() const
{
    std::lock_guard<std::mutex> guard(_mutex);
    return _scenario;       // a copy made under the lock, same rule as Registry::snapshot
}