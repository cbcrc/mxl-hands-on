// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#include "domains.hpp"

#include <algorithm>
#include <cstdio>
#include <filesystem>
#include <fstream>

#include <nlohmann/json.hpp>

namespace fs = std::filesystem;

namespace
{
    constexpr double kDefaultBufferDepthMs = 200.0;
    constexpr char const* kHistoryDurationKey = "urn:x-mxl:option:history_duration/v1.0";

    nlohmann::json readJson(fs::path const& path)
    {
        std::ifstream file(path);
        return nlohmann::json::parse(file);
    }
}

std::vector<DomainInfo> scanDomains(std::string const& root)
{
    std::vector<DomainInfo> found;

    std::error_code ec;
    if (!fs::exists(root, ec))
    {
        std::fprintf(stderr, "domain root %s does not exist\n", root.c_str());
        return found;
    }

    for (auto const& entry : fs::recursive_directory_iterator(root, ec))
    {
        if (entry.path().filename() != "domain_def.json")
        {
            continue;
        }

        try
        {
            auto const def = readJson(entry.path());

            DomainInfo info;
            info.id                   = def.value("id", "unknown");
            info.label                = def.value("label", "");
            info.description          = def.value("description", "");
            info.path                 = entry.path().parent_path().string();
            info.bufferDepthMs        = kDefaultBufferDepthMs;
            info.bufferDepthIsDefault = true;

            auto const optionsPath = entry.path().parent_path() / "option.json";
            if (fs::exists(optionsPath, ec))
            {
                auto const options = readJson(optionsPath);
                if (options.contains(kHistoryDurationKey))
                {
                    info.bufferDepthMs        = options[kHistoryDurationKey].get<double>() / 1000000.0;
                    info.bufferDepthIsDefault = false;
                }
            }

            found.push_back(info);
        }
        catch (std::exception const& e)
        {
            std::fprintf(stderr, "could not parse %s: %s\n",
                         entry.path().string().c_str(), e.what());
        }
    }

    std::sort(found.begin(), found.end(),
              [](DomainInfo const& a, DomainInfo const& b) { return a.path < b.path; });
    return found;
}