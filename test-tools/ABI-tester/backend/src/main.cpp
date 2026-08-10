// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
//
// MXL ABI Tester - the backend that executes queued ABI calls.

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <cstring>
#include <cstdint>
#include <cstdlib>
#include <httplib.h>
#include <nlohmann/json.hpp>

#include <mxl/flow.h>
#include <mxl/mxl.h>
#include <mxl/time.h>
#include "domains.hpp"
#include "registry.hpp"
#include "calls.hpp"

// A record we stamp into the head of every grain payload, so the reader can
// measure true producer -> consumer transit instead of index-derived age.
struct PayloadStamp
{
    uint64_t magic;
    uint64_t index;
    uint64_t writeNs;
};

static_assert(sizeof(PayloadStamp) == 24, "PayloadStamp must be exactly 24 bytes");

static constexpr uint64_t kStampMagic = 0x4D584C5354414D50ULL;  // "MXLSTAMP"

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
    // Insert MXLSTAMP
    PayloadStamp stamp{};
    stamp.magic = kStampMagic;
    stamp.index = index;
    stamp.writeNs = mxlGetTime();

    std::memcpy(payload, &stamp, sizeof(stamp));
    // End of insert MXLSTAMP
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

    // mxlSleepForNs(2000000000ULL);   // Temporarily wait for 2 sec

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

    PayloadStamp readStamp{};
    std::memcpy(&readStamp, readPayload, sizeof(readStamp));

    if (readStamp.magic != kStampMagic)
    {
        std::fprintf(stderr, "  stamp: absent or corrupt (magic %llx)\n",
                    (unsigned long long)readStamp.magic);
    }
    else
    {
        int64_t transitNs = (int64_t)(wallNs - readStamp.writeNs);
        std::printf("  stamp index  : %llu\n", (unsigned long long)readStamp.index);
        std::printf("  transit      : %.3f ms (%lld ns)\n",
                    transitNs / 1000000.0, (long long)transitNs);
    }

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


    // Serve what we just measured, instead of sleeping on it.
    httplib::Server server;
    
    // The handle table every step will name its operands through.
    Registry registry;
    registry.store("main", HandleKind::Instance, instance, domain);

    server.Get("/health",
        [&](httplib::Request const&, httplib::Response& res)
        {
            nlohmann::json body;
            body["status"]      = "ok";
            body["sdk_version"] = version.full;
            body["domain"]      = domain;
            body["tmpfs"]       = isTmpFs;
            body["flow_id"]     = flowId;
            body["grain_index"] = readGrain.index;
            body["ots_ns"]      = otsNs;
            body["age_ms"]      = (int64_t)(mxlGetTime() - otsNs) / 1000000.0;

            res.set_content(body.dump(2) + "\n", "application/json");
        });
    
    // Calls for the mxl domain finder function.
    server.Get("/domains",
        [](httplib::Request const&, httplib::Response& res)
        {
            char const* root = std::getenv("MXL_DOMAIN_ROOT");

            nlohmann::json list = nlohmann::json::array();

            for (auto const& d : scanDomains(root ? root : "/Volumes/mxl"))
            {
                nlohmann::json item;
                item["id"]                      = d.id;
                item["label"]                   = d.label;
                item["description"]             = d.description;
                item["path"]                    = d.path;
                item["buffer_depth_ms"]         = d.bufferDepthMs;
                item["buffer_depth_is_default"] = d.bufferDepthIsDefault;
                list.push_back(item);
            }

            res.set_content(list.dump(2) + "\n", "application/json");
        });
    
    server.Get("/abi-calls",
        [](httplib::Request const&, httplib::Response& res)
        {
            nlohmann::json list = nlohmann::json::array();

            for (auto const& call : callCatalog())
            {
                nlohmann::json params = nlohmann::json::array();
                for (auto const& p : call.params)
                {
                    params.push_back(nlohmann::json{{"name", p.name},
                                                    {"type", p.type},
                                                    {"required", p.required},
                                                    {"description", p.description}});
                }

                list.push_back(nlohmann::json{{"name", call.name},
                                              {"header", call.header},
                                              {"description", call.description},
                                              {"params", params}});
            }

            res.set_content(list.dump(2) + "\n", "application/json");
        });
    
    server.Post("/step",
        [&](httplib::Request const& req, httplib::Response& res)
        {
            auto respond = [&res](int status, nlohmann::json body)
            {
                res.status = status;
                res.set_content(body.dump(2) + "\n", "application/json");
            };

            nlohmann::json const request = nlohmann::json::parse(req.body, nullptr, false);
            if (request.is_discarded() || !request.is_object() ||
                !request.contains("call") || !request["call"].is_string())
            {
                respond(400, nlohmann::json{{"ok", false},
                                            {"error", "body must be an object with a string \"call\""}});
                return;
            }

            std::string const name = request["call"].get<std::string>();

            CallSpec const* spec = findCall(name);
            if (spec == nullptr)
            {
                respond(404, nlohmann::json{{"ok", false}, {"error", "unknown call: " + name}});
                return;
            }

            nlohmann::json const args =
                request.contains("args") ? request["args"] : nlohmann::json::object();
            
            uint64_t const startNs = mxlGetTime();
            nlohmann::json result = spec->invoke(registry, args);
            uint64_t const endNs = mxlGetTime();

            if (!result.is_object())
            {
                respond(500, nlohmann::json{{"ok", false},
                                            {"error", "adapter for" + name + "did not return a JSON object"}});
                return;
            }

            result["call"]        = name;
            result["duration_us"] = (endNs - startNs) / 1000.0;

            respond(result.value("ok", false) ? 200 : 500, result);
        });

    server.Get("/state",
        [&](httplib::Request const&, httplib::Response& res)
        {
            nlohmann::json handles = nlohmann::json::object();

            for (auto const& [name, entry] : registry.snapshot())
            {
                char ptrText[32];
                std::snprintf(ptrText, sizeof(ptrText), "%p", entry.ptr);

                nlohmann::json item;
                item["kind"] = handleKindName(entry.kind);
                item["note"] = entry.note;
                item["ptr"] = ptrText;

                handles[name] = item;
            }

            res.set_content(handles.dump(2) + "\n", "application/json");
        });
    
    std::printf("Listening on http://0.0.0.0:9600 (Ctrl-C to stop)\n");
    server.listen("0.0.0.0", 9600);
    
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