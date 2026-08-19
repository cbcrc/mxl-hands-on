// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <mutex>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

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