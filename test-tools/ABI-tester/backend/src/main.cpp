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
#include "scenario.hpp"

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

    // The root /domains scans, distinct from the instance domain above. Named
    // here rather than inside the handler so the default lives in one place and
    // the server states it in the terminal it was actually launched from.
    char const* domainRootEnv = std::getenv("MXL_DOMAIN_ROOT");
    char const* domainRoot    = (domainRootEnv != nullptr) ? domainRootEnv : "/Volumes/mxl";
    std::printf("Domain root: %s\n", domainRoot);
    std::printf("Scenario dir: %s\n", scenarioDir().c_str());
    
    httplib::Server server;
    
    // The handle table every step will name its operands through.
    Registry registry;

    EventLog log;

    Engine engine(registry, log);
    
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
        [domainRoot](httplib::Request const&, httplib::Response& res)
        {
            nlohmann::json list = nlohmann::json::array();

            for (auto const& d : scanDomains(domainRoot))
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

    server.Get("/scenarios",
        [](httplib::Request const&, httplib::Response& res)
        {
            // nlohmann converts any container of convertible values into an array
            // by itself -- no loop, no push_back. It finds the conversion through
            // a to_json overload for std::vector, chosen by argument type.
            nlohmann::json const list = listScenarios();
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

            nlohmann::json args =
                request.contains("args") ? request["args"] : nlohmann::json::object();
            
            // Same spelling as scenario step: fill is a sibling of args, not a member.
            if (request.contains("fill") && request["fill"].is_object())
            {
                args["fill"] = request["fill"];
            }

            // Same spelling as scenario step. /call has no lane and no resolver, so
            // "store_as" inside args still works too.
            if (request.contains("out") && request["out"].is_object() &&
                (request["out"].size() == 1) && request["out"].begin().value().is_string())
            {
                args["store_as"] = request["out"].begin().value();
            }
            
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
            res.set_content(engine.state().dump(2) + "\n", "application/json");
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

    server.Post("/scenario",
        [&](httplib::Request const& req, httplib::Response& res)
        {
            auto respond = [&res](int status, nlohmann::json body)
            {
                res.status = status;
                res.set_content(body.dump(2) + "\n", "application/json");
            };

            nlohmann::json const doc = nlohmann::json::parse(req.body, nullptr, false);
            if (doc.is_discarded())
            {
                respond(400, nlohmann::json{{"ok", false}, {"error", "body is not valid JSON"}});
                return;
            }

            std::string error;
            if (!engine.loadScenario(doc, error))
            {
                respond(400, nlohmann::json{{"ok", false}, {"error", error}});
                return;
            }

            res.set_content(engine.state().dump(2) + "\n", "application/json");
        });

    server.Get("/scenario",
        [&](httplib::Request const&, httplib::Response& res)
        {
            res.set_content(engine.scenario().dump(2) + "\n", "application/json");
        });
    
    // A regex route: the capture group arrives as req.matches[1], a std::smatch.
    // Deliberately permissive -- ([\w-]+) here would trun a bad name into httplib's
    // own empty-bodied 404 instead of our named 400.
    server.Get(R"(/scenarios/(.+))",
        [](httplib::Request const& req, httplib::Response& res)
        {
            auto respond = [&res](int status, nlohmann::json body)
            {
                res.status = status;
                res.set_content(body.dump(2) + "\n", "application/json");
            };

            std::string const name = req.matches[1].str();
            std::string       error;

            if (!validScenarioName(name, error))
            {
                respond(400, nlohmann::json{{"ok", false}, {"error", error}});
                return;
            }

            nlohmann::json doc;
            if (!readScenario(name, doc, error))
            {
                respond(404, nlohmann::json{{"ok", false}, {"error", error}});
                return;
            }

            res.set_content(doc.dump(2) + "\n", "application/json");
        });

    server.Post(R"(/scenarios/(.+))",
        [](httplib::Request const& req, httplib::Response& res)
        {
            auto respond = [&res](int status, nlohmann::json body)
            {
                res.status = status;
                res.set_content(body.dump(2) + "\n", "application/json");
            };

            nlohmann::json const doc = nlohmann::json::parse(req.body, nullptr, false);
            if (doc.is_discarded())
            {
                respond(400, nlohmann::json{{"ok", false}, {"error", "body is not valid JSON"}});
                return;
            }

            std::string const name = req.matches[1].str();
            std::string       error;

            if (!writeScenario(name, doc, error))
            {
                respond(400, nlohmann::json{{"ok", false}, {"error", error}});
                return;
            }

            respond(200, nlohmann::json{{"ok", true}, {"saved_as", name}});
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
                !request.contains("lane") || !request["lane"].is_string())
            {
                respond(400, nlohmann::json{{"ok", false},
                                            {"error", "body must be an object with a string \"lane\""}});
                return;
            }

            std::string error;
            if (!engine.stepOnce(request["lane"].get<std::string>(), error))
            {
                respond(400, nlohmann::json{{"ok", false}, {"error", error}});
                return;
            }

            res.set_content(engine.state().dump(2) + "\n", "application/json");
        });
    
    server.Post("/run",
        [&](httplib::Request const& req, httplib::Response& res)
        {
            nlohmann::json const request = nlohmann::json::parse(req.body, nullptr, false);
            if (!request.is_discarded() && request.is_object() &&
                request.contains("delay_scale") && request["delay_scale"].is_number())
            {
                engine.setDelayScale(request["delay_scale"].get<double>());
            }

            engine.run();
            res.set_content(engine.state().dump(2) + "\n", "application/json");
        });
    
    server.Post("/pause",
        [&](httplib::Request const&, httplib::Response& res)
        {
            engine.pause();
            res.set_content(engine.state().dump(2) + "\n", "application/json");
        });
    
    server.Post("/reset",
        [&](httplib::Request const&, httplib::Response& res)
        {
            res.set_content(engine.reset().dump(2) + "\n", "application/json");
        });
    
    std::printf("Listening on http://0.0.0.0:9600 (Ctrl-C to stop)\n");
    server.listen("0.0.0.0", 9600);
            
    return 0;
}