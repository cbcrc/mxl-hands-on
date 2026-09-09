// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <functional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>
#include <mxl/rational.h>

#include "registry.hpp"

// One argument a call accepts. The frontend builds its form from these.
struct ParamSpec
{
    std::string name;
    std::string type;           // "string" | "uint64" | "index" | "handle" | "rational"
    bool        required;
    std::string description;
};

// One MXL ABI call: what it is, what it takes, and how to perform it.
struct CallSpec
{
    std::string            name;
    std::string            header;         // "mxl.h" | "flow.h" | "time.h"
    std::string            description;
    std::vector<ParamSpec> params;

    std::function<nlohmann::json(Registry&, nlohmann::json const&)> invoke;
};

// The whole catalog, built once on first use.
std::vector<CallSpec> const& callCatalog();

// One entry by ABI name, or nullptr.
CallSpec const* findCall(std::string const& name);

// Parse args[name] as {"num":<int>, "den":<non-zero int>}. Shared with the engine:
// the `current` index mode needs the same validation the time.h adapters use.
bool argRational(nlohmann::json const& args, char const* name, mxlRational& out);
