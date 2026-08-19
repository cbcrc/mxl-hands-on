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
#include "engine.hpp"

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
    
    httplib::Server server;
    
    // The handle table every step will name its operands through.
    Registry registry;

    EventLog log;
    
    server.Get("/health",
        [&](httplib::Request const&, httplib::Response& res)
        {
            nlohmann::json body;
            body["status"]      = "ok";
            body["sdk_version"] = version.full;
            body["domain"]      = domain;
            body["tmpfs"]       = isTmpFs;

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
    
    server.Post("/call",
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
            result["seq"]         = log.append("-", "", endNs, result);

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

    server.Get("/log",
        [&](httplib::Request const& req, httplib::Response& res)
        {
            uint64_t sinceSeq = 0;
            if (req.has_param("since"))
            {
                sinceSeq = std::strtoull(req.get_param_value("since").c_str(), nullptr, 10);
            }
            res.set_content(log.since(sinceSeq).dump(2) + "\n", "application/json");
        });
    
    std::printf("Listening on http://0.0.0.0:9600 (Ctrl-C to stop)\n");
    server.listen("0.0.0.0", 9600);
            
    return 0;
}