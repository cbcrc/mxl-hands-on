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
};

char const* handleKindName(HandleKind kind);

// One named, live MXL handle.
struct HandleEntry
{
    HandleKind  kind;
    void*       ptr;
    std::string note;   // where it came from: a domain path, a flow id, ...
};

// A named table of live MXL handles, safe to touch from several lane threads.
class Registry
{
public:
    void store(std::string const& name, HandleKind kind, void* ptr, std::string note);

    // The handle stored under `name`, or nullptr if absent or of a different kind.
    void* find(std::string const& name, HandleKind kind) const;

    // Remove the slot named `name` and return the handle it held, or nullptr if
    // it was absent or of a different kind. Removal and lookup happen under one
    // lock, so exactly one caller can ever receive a given pointer -- which is
    // what makes it safe to destroy it afterwards.
    void* take(std::string const& name, HandleKind kind);

    // A copy of every slot, for GET /state.
    std::map<std::string, HandleEntry> snapshot() const;

private:
    mutable std::mutex                  _mutex;
    std::map<std::string, HandleEntry>  _entries;
};
