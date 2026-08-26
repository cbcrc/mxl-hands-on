// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <mutex>
#include <string>
#include <vector>
#include <atomic>
#include <thread>
#include <map>
#include <condition_variable>
#include <deque>
#include <fstream>

#include <nlohmann/json.hpp>
#include "registry.hpp"

// The append-only trace of everything the tool has executed. Both lane threads and
// every HTTP handler touch it, so every method takes the lock.

// Events are nlohmann::ordered_json, not json: json is std::map-backed and sorts its
//keys, which would scatter seq/lane/call across each console line.
class EventLog
{
public:
    // Opens ABI_TESTER_LOG_FILE if it is set. Uset means no file, so the existing
    // workflow is unchanged.
    EventLog();
    ~EventLog();

    // Append one executed step. `result` is the adapter's own object, merged in flat.
    // Returns the seq this event was given.
    uint64_t append(std::string const& lane, std::string const& stepId,
                    uint64_t wallNs, nlohmann::json const& result);
    
    // Every event with seq > sinceSeq, oldest first, as a JSON array. false + `error`
    // when those events have already been trimmed: returning [] would read exactly
    // like "nothing new since you last asked".
    bool since(uint64_t sinceSeq, nlohmann::ordered_json& out, std::string& error) const;

    // Also rotates the file: /reset restarts seq at 1, and two runs sharing one file
    // would make the sequence ambiguous to every offline tool.
    void clear();

private:
    void trim();        // called with _mutex already held
    bool openRun();     // opens the file for run _run; false + stderr on failure
    void flushLoop();   // the flusher thread's body
    // Copies out what the file has not seen, writes it with the lock released.
    // Called with `lock` held; returns holding it.
    void drainLocked(std::unique_lock<std::mutex>& lock);

    mutable std::mutex _mutex;

    // A deque, not a vector: events arrive at the back and leave from the front, and
    // vector has no O(1) pop_front -- it would copy the whole tail down by one on every
    // append. Random access still works, which since() needs.
    std::deque<nlohmann::ordered_json> _events;

    uint64_t _baseSeq = 1;      // the seq of _events.front(), replacing "_events[i] is seq i+1"
    uint64_t _lastSeq = 0;      // the seq most recently handed out
    uint64_t _flushedSeq = 0;   // the seq of the last event written to disk; trim() stops here

    std::string _path;          // ABI_TESTER_LOG_FILE as given; empty when unset
    unsigned    _run = 1;       // rotation counter; /reset opens run 2, 3, ...

    std::ofstream _file;        
    bool _fileOpen = false;     // _file.is_open(), but safe to read under _mutex -- see trim()

    bool _rotate   = false;     // set by clear(), cleared by the flusher once it has rotated
    bool _shutdown = false;
    std::condition_variable _flushWake;
    std::thread _flusher;       // last member: constructed after everything it touches
};

// One queued ABI call, exactly as the scenario JSON spells it. `args` is passed to
// the adapter verbatim -- index modes and fill are a later transformation.
struct Step
{
    std::string    id;
    std::string    call;
    double         delayBeforeMs = 0.0;
    nlohmann::json args = nlohmann::json::object();
    int64_t        advanceCursor = 0; // added to the lane cursor after the step runs
    nlohmann::json fill;    // step-level; merged into args after index resolution
    std::string    out;     // step-level registry name; merge into args as "store_as"

    // Absolute pacing: sleep until this index's own OTS instead of a relative delay.
    // Mutually exclusive with delayBeforeMs, refused in parseLane.
    bool           paced = false;
    double         paceOffsetMs = 0.0; // negative is legal: a reader lane trailing the writer

    // "repeat" pseudo-step only. The target is a step *position*, resolved at load time
    // so the jump costs no id lookup. The live counter is in Lane, not here
    std::size_t    repeatTo    = 0;
    uint64_t       repeatTimes = 0; 
};

// One lane: a name, its step list, and where it is in that list.
struct Lane
{
    std::string       name;
    std::vector<Step> steps;
    std::size_t       next = 0;     // index of the step that runs next
    uint64_t          cursor = 0; // the index the scenario advances by hand, not the wall clock
    
    // One slot per step; only a repeat step uses its own. Lane state, not step state:
    // /reset re-seeds it from the Steps so the scenario runs identically the second time.
    std::vector<uint64_t> repeatsLeft;
};

// Owns the two lanes and the transport. Borrows the registry and the log; both
// outlive it, because both are locals of main() declared before it.
class Engine
{
    public:
        // Replace both lanes from a scenario document. On failure returns false with
        // `error` set and *nothing changed*.
        bool loadScenario(nlohmann::json const& doc, std::string& error);

        // The whole tool's state for GET /state: lanes + handles.
        nlohmann::ordered_json state() const;

        // Execute the next step of one lane. false + `error` if the lane name is unknown
        //  or the lane is finished.
        bool stepOnce(std::string const& laneName, std::string& error);

        Engine(Registry& registry, EventLog& log);
        ~Engine();

        // Non-copyable: the threads capture `this`, so a copy would alias them.
        Engine(Engine const&)            = delete;
        Engine& operator=(Engine const&) = delete;

        void run();                             // both lanes free-running
        void pause();                           // stop before the next step
        void setDelayScale(double scale);     // multiplies every delay_before_ms

        // Stop the lanes, release every handle, clear the log and both cursors.
        // The loaded scenario is kept -- you reset in order to run it again.
        nlohmann::ordered_json reset();

        // The scenario document exactly as it was loaded. Kept verbatim rather than
        // rebuilt from the steps: parseLane ignores "name", "description" and "note",
        // so a save-after-load rebuilt from Steps would quietly drop them.
        nlohmann::json scenario() const;
    
    private:
        Registry& _registry;
        EventLog& _log;
        
        // &_laneA / &_laneB / nullptr. No lock: the members' address never move.
        Lane* laneFor(std::string const& name);

        // Sleep the step's delay, invoke the adapter, log the event. Called with
        // _mutex NOT held -- and adapter may block for seconds. The cursor comes in
        // by value and the new one goes out by return: a reference into the Lane
        // would dangle for the same reason the Step is copied.
        uint64_t execute(std::string const& laneName, Step const& step, uint64_t cursor);

        void laneLoop(Lane& lane);

        mutable std::mutex _mutex;      // guards _lanes only, never the registry
        // The pool, built once in the ctor and never resized. A map's nodes have
        // stable addresses, so the Lane* laneFor hands out stays valid across the
        // unlocked execute() -- and an immutable pool is what makes that lookup
        // safe without _mutex.
        std::map<std::string, Lane> _lanes;
        nlohmann::json     _scenario = nlohmann::json::object();

        std::atomic<bool>   _running{false};
        std::atomic<int>    _busy{0};           //lane threads currently inside an adapter
        std::atomic<bool>   _resetting{false};
        std::atomic<bool>   _shutdown{false};
        std::atomic<double> _delayScale{1.0};

        // Lanes sleep on this instead of polling. Everything its predicate reads --
        // _shutdown, _running, _resetting, lane.next, lane.steps -- is written under
        // _mutex, which is the only thing that prevents a lost wakeup.
        std::condition_variable _wake;

        // Last: members are constructed in declaration order, ant these start running
        // immediately, so everything they touch must already exist.
        std::vector<std::thread> _threads;      // one per lane        
};