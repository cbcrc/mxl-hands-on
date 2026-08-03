// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
//
// MXL ABI Tester - the backend that executes queued ABI calls.

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>

#include <mxl/flow.h>
#include <mxl/mxl.h>
#include <mxl/time.h>

// File reader helper
static std::string readFile(char const* path)
{
    std::ifstream file(path);
    if (!file)
    {
        return {};
    }

    std::ostringstream buffer;
    buffer << file.rdbuf();
    return buffer.str();
}

// Main loop
int main(int argc, char** argv)
{
    // get MXL version
    mxlVersionType version{};

    mxlStatus status = mxlGetVersion(&version);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlGetVersion failed with status %d\n", status);
        return 1;
    }

    std::printf("MXL SDK version: %u.%u.%u (%s)\n",
                version.major, version.minor, version.bugfix, version.full);
    
    // check if mxl domain is tmpfs
    char const* domain = (argc > 1) ? argv[1] : "/Volumes/mxl/domain_1";
    std::printf("Domain: %s\n", domain);

    bool isTmpFs = false;
    status = mxlIsTmpFs(domain, &isTmpFs);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "warning: could not determine filesystem type for %s (status %d)\n",
                    domain, status);
    }
    else
    {
        std::printf("Domain on RAM disk: %s\n", isTmpFs ? "yes" : "no");
    }
    
    // Create MXL instance
    mxlInstance instance = mxlCreateInstance(domain, nullptr);
    if (instance == nullptr)
    {
        std::fprintf(stderr, "mxlCreateInstance failed for domain %s\n", domain);
        return 1;
    }

    std::printf("Instance created.\n");

    // load flow_def.json
    char const* flowDefPath = (argc > 2) ? argv[2] : "../flows/video-1080p2997-v210.json";

    std::string flowDef = readFile(flowDefPath);
    if (flowDef.empty())
    {
        std::fprintf(stderr, "could not read flow definition %s\n", flowDefPath);
        return 1;
    }

    // Create flow writer
    mxlFlowWriter       writer{};
    mxlFlowConfigInfo   config{};
    bool                created = false;

    status = mxlCreateFlowWriter(instance, flowDef.c_str(), nullptr, &writer, &config, &created);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlCreateFlowWriter failed with status %d\n", status);
        return 1;
    }

    std::printf("Flow writer ready (%s).\n", created ? "new flow" : "existing flow");
    std::printf("  grain rate : %lld/%lld\n",
                (long long)config.common.grainRate.numerator,
                (long long)config.common.grainRate.denominator);
    std::printf("  grain count: %u\n", config.discrete.grainCount);
    std::printf("  slice size : %u bytes\n", config.discrete.sliceSizes[0]);

    std::printf("Holding the flow open for 30 seconds - inspect it with mxl-info now.\n");
    mxlSleepForNs(30ULL * 1000ULL * 1000ULL * 1000ULL);
    
    // Release flow writer
    status = mxlReleaseFlowWriter(instance, writer);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlReleaseFlowWriter failed with status %d\n", status);
        return 1;
    }

    std::printf("Flow writer released.\n");

    // Destroy mxl instance
    status = mxlDestroyInstance(instance);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlDestroyInstance failed with status %d\n", status);
        return 1;
    }

    std::printf("Instance destroyed.\n");

    return 0;
}