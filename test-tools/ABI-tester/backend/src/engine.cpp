// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#include <cstdio>
#include <mxl/time.h>
#include <mxl/flow.h>
#include <mxl/mxl.h>

#include "engine.hpp"
#include "calls.hpp"

uint64_t EventLog::append(std::string const& lane, std::string const& stepId,
                          uint64_t wallNs, nlohmann::json const& result)
{
    std::lock_guard<std::mutex> guard(_mutex);

    uint64_t const seq = _events.size() + 1;

    nlohmann::ordered_json event;
    event["seq"]        = seq;
    event["lane"]       = lane;
    event["step_id"]    = stepId;
    event["t_wall_ns"] = std::to_string(wallNs);    // ns as a string: the M9 number contract
    event.update(result);                           // the adapter's own keys, flat

    _events.push_back(std::move(event));
    return seq;
}

nlohmann::ordered_json EventLog::since(uint64_t sinceSeq) const
{
    std::lock_guard<std::mutex> guard(_mutex);

    nlohmann::ordered_json list = nlohmann::ordered_json::array();

    for (std::size_t i = (std::size_t)sinceSeq; i < _events.size(); ++i)
    {
        list.push_back(_events[i]);
    }

    return list;
}

void EventLog::clear()
{
    std::lock_guard<std::mutex> guard(_mutex);
    _events.clear();
}

Engine::Engine(Registry& registry, EventLog& log)
    : _registry(registry)
    , _log(log)
{
    // Started in the body, not the member-init list: a thread launched there could
    // reach a member that has not been constructed yet.
    _threadA = std::thread([this] { laneLoop(_laneA); });
    _threadB = std::thread([this] { laneLoop(_laneB); });
}

Engine::~Engine()
{
    _shutdown = true;

    if (_threadA.joinable())
    {
        _threadA.join();
    }
    if (_threadB.joinable())
    {
        _threadB.join();
    }
}

namespace
{
    // One index expression -> one number. This is the only place the four modes
    // are spelled out; `current` and `head` join it later.
    bool resolveIndex(nlohmann::json const& expr, uint64_t cursor,
                      uint64_t&out, std::string& error)
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
    bool resolveArgs(nlohmann::json& args, uint64_t cursor,
                     nlohmann::json& resolved, std::string& error)
    {
        for (auto& [key, value] : args.items())
        {
            if (!value.is_object() || !value.contains("mode"))
            {
                continue;       // edit_rate's {num,den}, and every plain scalar
            }

            uint64_t index = 0;
            if (!resolveIndex(value, cursor, index, error))
            {
                error = key + ": " + error;
                return false;
            }

            value         = index;    // the adapter sees a number, as it always has
            resolved[key] = index;
        }
        return true;
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

            if (findCall(step.call) == nullptr)
            {
                error = std::string("lane ") + laneName + ": unknown call: " + step.call;
                return false;
            }

            step.id             = item.value("id", std::string{});
            step.delayBeforeMs  = item.value("delay_before_ms", 0.0);
            step.advanceCursor  = item.value("advance_cursor", (int64_t)0);
            if (item.contains("args") && item["args"].is_object())
            {
                step.args = item["args"];
            }

            out.push_back(std::move(step));
        }
        return true;
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

    // Parse into locals first. A lane B that fails must not leave lane A replaced --
    // and B is parsed only if A succeeded, because || short-circuits.
    std::vector<Step> stepsA;
    std::vector<Step> stepsB;

    if (!parseLane(lanes.contains("A") ? lanes["A"] : nlohmann::json::array(), "A", stepsA, error) ||
        !parseLane(lanes.contains("B") ? lanes["B"] : nlohmann::json::array(), "B", stepsB, error))
    {
        return false;
    }

    std::lock_guard<std::mutex> guard(_mutex);
    _laneA.steps = std::move(stepsA);
    _laneA.next  = 0;
    _laneB.steps = std::move(stepsB);
    _laneB.next  = 0;
    return true;
}

nlohmann::ordered_json Engine::state() const
{
    nlohmann::ordered_json lanes = nlohmann::ordered_json::object();
    {
        std::lock_guard<std::mutex> guard(_mutex);
        for (Lane const* lane : {&_laneA, &_laneB})
        {
            nlohmann::ordered_json item;
            item["steps"]     = lane->steps.size();
            item["next"]      = lane->next;
            item["cursor"]    = lane->cursor;
            lanes[lane->name] = item;
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
    body["lanes"]       = lanes;
    body["handles"]     = handles;
    return body;
}

Lane* Engine::laneFor(std::string const& name)
{
    if (name == "A")
    {
        return &_laneA;
    }
    if (name == "B")
    {
        return &_laneB;
    }
    return nullptr;
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

    // Resolve index expressions against *this* lane's cursor -- on a copy, so the
    // scenario itself is never rewritten and the same step re-runs identically
    // after a /reset.
    nlohmann::json args     = step.args;
    nlohmann::json resolved = nlohmann::json::object();
    std::string    error;

    if (!resolveArgs(args, cursor, resolved, error))
    {
        _log.append(laneName, step.id, mxlGetTime(),
                    nlohmann::json{{"ok", false}, {"call", step.call}, {"error", error}});
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

    if (!resolved.empty())
    {
        result["resolved"] = resolved;
    }

    _log.append(laneName, step.id, endNs, result);
    return (uint64_t)((int64_t)cursor + step.advanceCursor);
}

void Engine::laneLoop(Lane& lane)
{
    // lane.name is set at construction and never written again, so reading it
    // outside the lock is safe. steps/next are only ever touched by stepOnce.
    while (!_shutdown)
    {
        std::string error;

        if (!_running || !stepOnce(lane.name, error))
        {
            mxlSleepForNs(5000000);     // 5 ms: paused, or out of steps
        }
    }
}

void Engine::run()
{
    _running = true;
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
                _laneA.next   = 0;
                _laneA.cursor = 0;
                _laneB.next   = 0;
                _laneB.cursor = 0;
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