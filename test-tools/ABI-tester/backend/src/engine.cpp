// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#include "engine.hpp"

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