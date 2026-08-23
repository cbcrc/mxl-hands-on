// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <mutex>
#include <string>
#include <vector>
#include <atomic>
#include <thread>

#include <nlohmann/json.hpp>
#include "registry.hpp"

// The append-only trace of everything the tool has executed. Both lane threads and
// every HTTP handler touch it, so every method takes the lock.

// Events are nlohmann::ordered_json, not json: json is std::map-backed and sorts its
//keys, which would scatter seq/lane/call across each console line.
class EventLog
{
public:
    // Append one executed step. `result` is the adapter's own object, merged in flat.
    // Returns the seq this event was given.
    uint64_t append(std::string const& lane, std::string const& stepId,
                    uint64_t wallNs, nlohmann::json const& result);
    
    // Every event with seq > sinceSeq, oldest first, as a JSON array.
    nlohmann::ordered_json since(uint64_t sinceSeq) const;

    void clear();

private:
    mutable std::mutex                  _mutex;
    std::vector<nlohmann::ordered_json> _events;    // _events[i] carries seq i+1
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
};

// One lane: a name, its step list, and where it is in that list.
struct Lane
{
    std::string       name;
    std::vector<Step> steps;
    std::size_t       next = 0;     // index of the step that runs next
    uint64_t          cursor = 0; // the index the scenario advances by hand, not the wall clock
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

        mutable std::mutex _mutex;      // guards _laneA/_laneB only, never the registry
        Lane               _laneA{"A", {}, 0, 0};
        Lane               _laneB{"B", {}, 0, 0};

        std::atomic<bool>   _running{false};
        std::atomic<int>    _busy{0};           //lane threads currently inside an adapter
        std::atomic<bool>   _resetting{false};
        std::atomic<bool>   _shutdown{false};
        std::atomic<double> _delayScale{1.0};

        // Last: members are constructed in declaration order, ant these start running
        // immediately, so everything they touch must already exist.
        std::thread _threadA;
        std::thread _threadB;

        
};