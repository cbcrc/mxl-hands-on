// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <string>
#include <vector>

// One MXL domain found on disk.
struct DomainInfo
{
    std::string id;
    std::string label;
    std::string description;
    std::string path;
    double      bufferDepthMs;
    bool        bufferDepthIsDefault;
};

// Walk `root` recursively and return one entry per domain_def.json found.
std::vector<DomainInfo> scanDomains(std::string const& root);