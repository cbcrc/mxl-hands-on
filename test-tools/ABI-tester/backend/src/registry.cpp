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
        case HandleKind::Grain:      return "grain";
        case HandleKind::SyncGroup:  return "sync_group";
    }
    return "unknown";
}

bool Registry::store(std::string const& name, HandleEntry entry)
{
    std::lock_guard<std::mutex> lock(_mutex);

    // try_emplace inserts only if the key is absent, under this one lock.
    auto const [it, inserted] = _entries.try_emplace(name, std::move(entry));
    (void)it;
    return inserted;
}

bool Registry::store(std::string const& name, HandleKind kind, void* ptr, std::string note,
                     void* owner)
{
    return store(name, HandleEntry{kind, ptr, std::move(note), {}, nullptr, owner});
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

bool Registry::findEntry(std::string const& name, HandleKind kind, HandleEntry& out) const
{
    std::lock_guard<std::mutex> lock(_mutex);

    auto const it = _entries.find(name);
    if ((it == _entries.end()) || (it->second.kind != kind))
    {
        return false;
    }

    out = it->second; // copied under the lock, like snapshot()
    return true;
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

bool Registry::takeEntry(std::string const& name, HandleKind kind, HandleEntry& out)
{
    std::lock_guard<std::mutex> lock(_mutex);

    auto const it = _entries.find(name);
    if ((it == _entries.end()) || (it->second.kind != kind))
    {
        return false;
    }

    out = std::move(it->second);
    _entries.erase(it);
    return true;
}

std::map<std::string, HandleEntry> Registry::snapshot() const
{
    std::lock_guard<std::mutex> lock(_mutex);
    return _entries; // copied under the lock, so the caller holds no reference into it
}

std::map<std::string, HandleEntry> Registry::drain()
{
    std::lock_guard<std::mutex> lock(_mutex);

    std::map<std::string, HandleEntry> taken;
    taken.swap(_entries);   // removed and handed over in one locked operation
    return taken;
}