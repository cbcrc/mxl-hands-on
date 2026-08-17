// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <map>
#include <mutex>
#include <string>

#include <mxl/flow.h>
#include <mxl/mxl.h>

// What kind MXL handle a registry slot holds.
enum class HandleKind
{
    Instance,
    FlowWriter,
    FlowReader,
    Grain,
    SyncGroup,
};

char const* handleKindName(HandleKind kind);

// One named, live MXL handle.
struct HandleEntry
{
    HandleKind  kind;
    void*       ptr;    // the MXL handle; for a Grain slot, the writer/reader it came from
    std::string note;   // where it came from: a domain path, a flow id, ...

    // Grain slots only. CommitGrain must be handed back the same mxlGrainInfo that
    // OpenGrain produced, and the payload pointer is where a fill step writes, so
    // both have to outlive the request that opened the grain.
    mxlGrainInfo grain{};
    uint8_t*     payload = nullptr;
};

// A named table of live MXL handles, safe to touch from several lane threads.
class Registry
{
public:
    // Store a fully-built entry. The only way to store a Grain slot.
    bool store(std::string const& name, HandleEntry entry);

    bool store(std::string const& name, HandleKind kind, void* ptr, std::string note = {});

    // The handle stored under `name`, or nullptr if absent or of a different kind.
    void* find(std::string const& name, HandleKind kind) const;

    // A copy of the whole slot rather than just its pointer -- a Grain slot's payload
    // and cached mxlGrainInfo do not fit through find(). false if absent or wrong kind.
    bool findEntry(std::string const& name, HandleKind kind, HandleEntry& out) const;

    // Remove the slot named `name` and return the handle it held, or nullptr if
    // it was absent or of a different kind. Removal and lookup happen under one
    // lock, so exactly one caller can ever receive a given pointer -- which is
    // what makes it safe to destroy it afterwards.
    void* take(std::string const& name, HandleKind kind);

    // take(), for the slots that need the whole entry. Same one-lock guarantee.
    bool takeEntry(std::string const& name, HandleKind kind, HandleEntry& out);

    // A copy of every slot, for GET /state.
    std::map<std::string, HandleEntry> snapshot() const;

private:
    mutable std::mutex                  _mutex;
    std::map<std::string, HandleEntry>  _entries;
};
