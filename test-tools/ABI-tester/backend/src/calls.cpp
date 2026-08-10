// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#include "calls.hpp"

#include <cstdio>

#include <mxl/mxl.h>
#include <mxl/time.h>

using nlohmann::json;

namespace
{
    // --- result helpers ----------------------------------------

    json failed(std::string const& message)
    {
        return json{{"ok", false}, {"error", message}};
    }

    // Large uint64 values go out as strings: JSON numbers lose precision above 2^53.
    std::string nsText(uint64_t value)
    {
        char text[24];
        std::snprintf(text, sizeof(text), "%llu", (unsigned long long)value);
        return text;
    }

    // --- argument accessors -------------------------------------

    std::string argString(json const& args, char const* name, std::string fallback = {})
    {
        auto const it = args.find(name);
        return ((it != args.end()) && it->is_string()) ? it->get<std::string>() : fallback;
    }

    // --- The catalog --------------------------------------------

    std::vector<CallSpec> buildCatalog()
    {
        std::vector<CallSpec> calls;

        calls.push_back(CallSpec{
            "mxlGetTime", "time.h",
            "Current TAI time in nanoseconds since the SMPTE ST 2059 epoch.",
            {},
            [](Registry&, json const&)
            {
                return json{{"ok", true}, {"time_ns", nsText(mxlGetTime())}};
            }});
        
        calls.push_back(CallSpec{
            "mxlGetVersion", "mxl.h",
            "The SDK's semantic version.",
            {},
            [](Registry&, json const&)
            {
                mxlVersionType version{};
                mxlStatus const status = mxlGetVersion(&version);
                if (status != MXL_STATUS_OK)
                {
                    return failed("mxlGetVersion returned status " + std::to_string(status));
                }
                return json{{"ok", true}, {"version", version.full},
                            {"major", version.major}, {"minor", version.minor},
                            {"bugfix", version.bugfix}};
            }});
        
        calls.push_back(CallSpec{
            "mxlIsTmpFs", "mxl.h",
            "Whether a path resides on a RAM-backed filesystem.",
            {{"path", "string", true, "Directory to test"}},
            [](Registry&, json const& args)
            {
                std::string const path = argString(args, "path");
                if (path.empty())
                {
                    return failed("argument 'path' is required");
                }

                bool isTmpFs = false;
                mxlStatus const status = mxlIsTmpFs(path.c_str(), &isTmpFs);
                if (status != MXL_STATUS_OK)
                {
                    return failed("mxlIsTmpFs returned status " + std::to_string(status));
                }
                return json{{"ok", true}, {"is_tmpfs", isTmpFs}};
            }});
        
        return calls;
    }
}

std::vector<CallSpec> const& callCatalog()
{
    static std::vector<CallSpec> const catalog = buildCatalog();
    return catalog;
}

CallSpec const* findCall(std::string const& name)
{
    for (auto const& call : callCatalog())
    {
        if (call.name == name)
        {
            return &call;
        }
    }
    return nullptr;
}