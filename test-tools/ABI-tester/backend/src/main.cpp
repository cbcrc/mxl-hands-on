// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
//
// MXL ABI Tester - the backend that executes queued ABI calls.

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <cstring>

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
    
    // Open, fill and commit one grain
    uint64_t index = mxlGetCurrentIndex(&config.common.grainRate);

    mxlGrainInfo grain{};
    uint8_t*    payload = nullptr;

    status = mxlFlowWriterOpenGrain(writer, index, &grain, &payload);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlFlowWriterOpenGrain failed with status %d\n", status);
        return 1;
    }

    std::printf("Grain %llu opened: %u bytes, %u slices.\n",
                (unsigned long long)index, grain.grainSize, grain.totalSlices);
    
    std::memset(payload, 0, grain.grainSize);
    grain.validSlices = grain.totalSlices;

    status = mxlFlowWriterCommitGrain(writer, &grain);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlFlowWriterCommitGrain failed with status %d\n", status);
        return 1;
    }

    std::printf("Grain %llu commited.\n", (unsigned long long)index);

    // Attach a reader to the flow we just wrote
    char const* flowId = "a1b2c3d4-0001-4000-8000-000000000001";

    mxlFlowReader reader{};

    status = mxlCreateFlowReader(instance, flowId, nullptr, &reader);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlCreateFlowReader failed with status %d\n", status);
        return 1;
    }

    mxlGrainInfo readGrain{};
    uint8_t*    readPayload = nullptr;

    mxlSleepForNs(2000000000ULL);   // Temporarily wait for 2 sec

    status = mxlFlowReaderGetGrain(reader, index, 1000000000ULL, &readGrain, &readPayload);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlFlowReaderGetGrain failed with status %d\n", status);
        return 1;
    }

    std::printf("Read grain %llu: %u bytes, %u/%u slices valid.\n",
                (unsigned long long)readGrain.index, readGrain.grainSize,
                readGrain.validSlices, readGrain.totalSlices);

    // The index IS the timestamp
    uint64_t otsNs = mxlIndexToTimestamp(&config.common.grainRate, readGrain.index);
    uint64_t wallNs = mxlGetTime();
    int64_t ageNs = (int64_t)(wallNs -  otsNs);

    std::printf("  ots  : %llu ns\n", (unsigned long long)otsNs);
    std::printf("  wall : %llu ns\n", (unsigned long long)wallNs);
    std::printf("  age  : %.3f ms\n", ageNs / 1000000.0);

    mxlFlowRuntimeInfo runtime{};

    status = mxlFlowReaderGetRuntimeInfo(reader, &runtime);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlFlowReaderGetRuntimeInfo failed with status %d\n", status);
        return 1;
    }

    std::printf("  head : %llu\n", (unsigned long long)runtime.headIndex);
    std::printf("  last write: %llu ns\n", (unsigned long long)runtime.lastWriteTime);
    std::printf("  last read : %llu ns\n", (unsigned long long)runtime.lastReadTime);
    
    mxlSleepForNs(100000000ULL); // 100 ms - let the watcher thread run

    mxlFlowRuntimeInfo runtime2{};
    (void)mxlFlowReaderGetRuntimeInfo(reader, &runtime2);
    std::printf("  last read (after 100 ms): %llu ns (delta %.3f ms)\n",
                (unsigned long long)runtime2.lastReadTime,
                (int64_t)(runtime2.lastReadTime - runtime.lastReadTime) / 1000000.0);


    std::printf("Holding the flow open for 30 seconds - inspect it with mxl-info now.\n");
    mxlSleepForNs(30ULL * 1000ULL * 1000ULL * 1000ULL);
    
    // Release flow reader
    status = mxlReleaseFlowReader(instance, reader);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlReleaseFlowReader failed with status %d\n", status);
        return 1;
    }

    // Release flow writer
    status = mxlReleaseFlowWriter(instance, writer);
    if (status != MXL_STATUS_OK)
    {
        std::fprintf(stderr, "mxlReleaseFlowWriter failed with status %d\n", status);
        return 1;
    }

    std::printf("Flow writer and reader released.\n");

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