// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#include "registry.hpp"

#include <utility>

char const* handleKindName(HandleKind kind)
{
    switch (kind)
    {
        case HandleKind::Instance:   return "instance";
        case HandleKind::FlowWriter: return "flow_writer";
        case HandleKind::FlowReader: return "flow_reader";
    }
    return "unknown";
}

void Registry::store(std::string const& name, HandleKind kind, void* ptr, std::string note)
{
    std::lock_guard<std::mutex> lock(_mutex);
    _entries[name] = HandleEntry{kind, ptr, std::move(note)};
}

void* Registry::find(std::string const& name, HandleKind kind) const
{
    std::lock_guard<std::mutex> lock(_mutex);

    auto const it = _entries.find(name);
    if ((it == _entries.end()) || (it->second.kind != kind))
    {
        return nullptr;
    }

    return it->second.ptr;
}

void* Registry::take(std::string const& name, HandleKind kind)
{
    std::lock_guard<std::mutex> lock(_mutex);

    auto const it = _entries.find(name);
    if ((it == _entries.end()) || (it->second.kind != kind))
    {
        return nullptr;
    }

    void* const ptr = it->second.ptr;
    _entries.erase(it);
    return ptr;
}

std::map<std::string, HandleEntry> Registry::snapshot() const
{
    std::lock_guard<std::mutex> lock(_mutex);
    return _entries; // copied under the lock, so the caller holds no reference into it
}