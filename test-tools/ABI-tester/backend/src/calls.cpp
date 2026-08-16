// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#include "calls.hpp"

#include <cstdio>
#include <cerrno>
#include <cstdlib>

#include <mxl/mxl.h>
#include <mxl/time.h>
#include <mxl/dataformat.h>
#include <mxl/flow.h>

using nlohmann::json;

namespace
{
    // --- result helpers ----------------------------------------

    json failed(std::string const& message)
    {
        return json{{"ok", false}, {"error", message}};
    }

    char const* statusName(mxlStatus status)
    {
        switch (status)
        {
            case MXL_STATUS_OK:                  return "MXL_STATUS_OK";
            case MXL_ERR_UNKNOWN:                return "MXL_ERR_UNKNOWN";
            case MXL_ERR_FLOW_NOT_FOUND:         return "MXL_ERR_FLOW_NOT_FOUND";
            case MXL_ERR_OUT_OF_RANGE_TOO_LATE:  return "MXL_ERR_OUT_OF_RANGE_TOO_LATE";
            case MXL_ERR_OUT_OF_RANGE_TOO_EARLY: return "MXL_ERR_OUT_OF_RANGE_TOO_EARLY";
            case MXL_ERR_INVALID_FLOW_READER:    return "MXL_ERR_INVALID_FLOW_READER";
            case MXL_ERR_INVALID_FLOW_WRITER:    return "MXL_ERR_INVALID_FLOW_WRITER";
            case MXL_ERR_TIMEOUT:                return "MXL_ERR_TIMEOUT";
            case MXL_ERR_INVALID_ARG:            return "MXL_ERR_INVALID_ARG";
            case MXL_ERR_CONFLICT:               return "MXL_ERR_CONFLICT";
            case MXL_ERR_PERMISSION_DENIED:      return "MXL_ERR_PERMISSION_DENIED";
            case MXL_ERR_FLOW_INVALID:           return "MXL_ERR_FLOW_INVALID";
            default:                             return "MXL_ERR_(other)";
        }
    }

    // The common envelope for any call that returns an mxlStatus. Adapters add
    // their out-params to it after checking the status themselves.
    json statusJson(char const* call, mxlStatus status)
    {
        json result;
        result["ok"]     = (status == MXL_STATUS_OK);
        result["status"] = statusName(status);
        if (status != MXL_STATUS_OK)
        {
            result["error"] = std::string(call) + " returned " + statusName(status);
        }
        return result;
    }

    // Large uint64 values go out as strings: JSON numbers lose precision above 2^53.
    std::string nsText(uint64_t value)
    {
        char text[24];
        std::snprintf(text, sizeof(text), "%llu", (unsigned long long)value);
        return text;
    }

    // The 16 raw bytes of a flow id as canonical 8-4-4-4-12 UUID text.
    std::string uuidText(uint8_t const (&id)[16])
    {
        char text[37];
        std::snprintf(text, sizeof(text),
            "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
            id[0], id[1], id[2], id[3], id[4], id[5], id[6], id[7],
            id[8], id[9], id[10], id[11], id[12], id[13], id[14], id[15]);
        return text;
    }

    // A sleep runs on the HTTP handler's own thread, so it must stay short enough
    // that the connection never looks hung.
    constexpr uint64_t kMaxSleepNs = 5'000'000'000ULL;

    // --- argument accessors -------------------------------------

    std::string argString(json const& args, char const* name, std::string fallback = {})
    {
        auto const it = args.find(name);
        return ((it != args.end()) && it->is_string()) ? it->get<std::string>() : fallback;
    }

    // Resolve the handle named by args[argName]. On failure returns nullptr and puts the
    // reason in `error` -- the caller turns that into failed(error)
    void* handleArg(Registry& registry, json const& args, char const* argName,
                    HandleKind kind, std::string& error)
    {
        std::string const name = argString(args, argName);
        if (name.empty())
        {
            error = std::string("argument '") + argName + "' is required";
            return nullptr;
        }

        void* const ptr = registry.find(name, kind);
        if (ptr == nullptr)
        {
            error = std::string("no ") + handleKindName(kind) + " handle named " + name;
        }
        return ptr;
    }

    bool argRational(json const& args, char const* name, mxlRational& out)
    {
        auto const it = args.find(name);
        if ((it == args.end()) || !it->is_object())
        {
            return false;
        }

        auto const num = it->find("num");
        auto const den = it->find("den");
        if ((num == it->end()) || (den == it->end()) ||
            !num->is_number_integer() || !den->is_number_integer())
        {
            return false;
        }

        out.numerator   = num->get<int64_t>();
        out.denominator = den->get<int64_t>();
        return out.denominator != 0;
    }

    // Accepts a JSON number *or* a decimal string. Above 2^53 the frontend can only
    // send these as strings, so both spellings have to work.
    bool argUint64(json const& args, char const* name, uint64_t& out)
    {
        auto const it = args.find(name);
        if (it == args.end())
        {
            return false;
        }

        if (it->is_number_unsigned())
        {
            out = it->get<uint64_t>();
            return true;
        }

        if (!it->is_string())
        {
            return false;
        }

        std::string const text  = it->get<std::string>();
        char const* const start = text.c_str();
        char*             end   = nullptr;
        
        errno = 0;
        unsigned long long const value = std::strtoull(start, &end, 10);
        if ((end == start) || (*end != '\0') || (errno == ERANGE))
        {
            return false; // nothing consumed / trailing junk / overflow
        }

        out = value;
        return true;
    }

    // --- The catalog --------------------------------------------

    std::vector<CallSpec> buildCatalog()
    {
        std::vector<CallSpec> calls;

        // ABI call mxlGetTime
        calls.push_back(CallSpec{
            "mxlGetTime", "time.h",
            "Current TAI time in nanoseconds since the SMPTE ST 2059 epoch.",
            {},
            [](Registry&, json const&)
            {
                return json{{"ok", true}, {"time_ns", nsText(mxlGetTime())}};
            }});
        
        // ABI call mxlGetVersion
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
        
        // ABI call mxlIsTmpFs
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
        
        // ABI call mxlCreateInstance
        calls.push_back(CallSpec{
            "mxlCreateInstance", "mxl.h",
            "Open an MXL domain and return an instance handle. Returns NULL on failure -- this call has no status code.",
            {{"domain", "string", true, "Domain directory, e.g. /Volumes/mxl/domain_1"},
             {"store_as", "handle", true, "Registry name to store the new instance under"},
             {"options", "string", false, "Reserved by the SDK; currently unused"}},
            [](Registry& registry, json const& args)
            {
                std::string const domain  = argString(args, "domain");
                std::string const storeAs = argString(args, "store_as");
                if (domain.empty() || storeAs.empty())
                {
                    return failed("arguments 'domain' and 'store_as' are required");
                }

                std::string const options = argString(args, "options");
                mxlInstance const instance =
                    mxlCreateInstance(domain.c_str(), options.empty() ? nullptr : options.c_str());
                if (instance == nullptr)
                {
                    return failed("mxlCreateInstance returned NULL for domain " + domain);
                }

                if (!registry.store(storeAs, HandleKind::Instance, instance, domain))
                {
                    mxlDestroyInstance(instance);   // nothing else can reach it -- undo the create
                    return failed("handle name '" + storeAs + "' is already in use");
                }
                return json{{"ok", true}, {"stored_as", storeAs}};
            }});
        
        // ABI call mxlDestroyInstance
        calls.push_back(CallSpec{
            "mxlDestroyInstance", "mxl.h",
            "Destroy an instance and every flow reader and writer it owns.",
            {{"instance", "handle", true, "Registry name of the instance to destroy"}},
            [](Registry& registry, json const& args)
            {
                std::string const name = argString(args, "instance");
                if (name.empty())
                {
                    return failed("argument 'instance' is required");
                }

                // take(), not find(): the slot is gone before the pointer is used,
                // so a second thread asking to destroy the same name gets nullptr
                // rather than a second crack at the same instance.
                void* const ptr = registry.take(name, HandleKind::Instance);
                if (ptr == nullptr)
                {
                    return failed("no instance handle named " + name);
                }
                
                mxlStatus const status = mxlDestroyInstance(static_cast<mxlInstance>(ptr));

                json result = statusJson("mxlDestroyInstance", status);
                result["released"] = name;
                return result;
            }});
        
        // ABI call mxlCreateFlowWriter
        calls.push_back(CallSpec{
            "mxlCreateFlowWriter", "flow.h",
            "Create a flow from a flow definition -- or open the existing one with that id -- and return a writer handle.",
            {{"instance", "handle", true, "Registry name of the instance"},
             {"flow_def", "string", true, "The flow definition JSON, as a string"},
             {"options", "string", false, "Reserved by the SDK, currently unused"},
             {"store_as", "handle", true, "Registry name to store the new writer under"}},
            [](Registry& registry, json const& args)
            {
                std::string const flowDef      = argString(args, "flow_def");
                std::string const storeAs      = argString(args, "store_as");
                if (flowDef.empty() || storeAs.empty())
                {
                    return failed("arguments 'flow_def' and 'store_as' are required");
                }

                std::string error;
                auto const  instance = static_cast<mxlInstance>(
                    handleArg(registry, args, "instance", HandleKind::Instance, error));
                if (instance == nullptr)
                {
                    return failed(error);
                }

                std::string const options = argString(args, "options");

                mxlFlowWriter     writer{};
                mxlFlowConfigInfo config{};
                bool              created = false;

                mxlStatus const status = mxlCreateFlowWriter(instance, flowDef.c_str(),
                    options.empty() ? nullptr : options.c_str(), &writer, &config, &created);

                json result = statusJson("mxlCreateFlowWriter", status);
                if (status != MXL_STATUS_OK)
                {
                    return result;
                }

                std::string const flowId = uuidText(config.common.id);
                if (!registry.store(storeAs, HandleKind::FlowWriter, writer, flowId))
                {
                    mxlReleaseFlowWriter(instance, writer);
                    return failed("handle name '" + storeAs + "' is already in use");
                }

                result["stored_as"]  = storeAs;
                result["flow_id"]    = flowId;
                result["created"]    = created;
                result["grain_rate"] = std::to_string(config.common.grainRate.numerator) + "/" +
                                       std::to_string(config.common.grainRate.denominator);
                if (mxlIsDiscreteDataFormat((int)config.common.format))
                {
                    result["grain_count"] = config.discrete.grainCount;
                }
                return result;
        }});

        // ABI call mxlReleaseFlowWriter
        calls.push_back(CallSpec{
            "mxlReleaseFlowWriter", "flow.h",
            "Release a flow writer. The flow itself stays in the domain until every writer and reader is gone.",
            {{"instance", "handle", true, "Registry name of the instance that owns the writer"},
                {"writer", "handle", true, "Registry name of the writer to release"}},
            [](Registry& registry, json const& args)
            {
                std::string const writerName   = argString(args, "writer");
                if (writerName.empty())
                {
                    return failed("argument 'writer' is required");
                }
                std::string error;
                auto const  instance = static_cast<mxlInstance>(
                    handleArg(registry, args, "instance", HandleKind::Instance, error));
                if (instance == nullptr)
                {
                    return failed(error);
                }

                void* const ptr = registry.take(writerName, HandleKind::FlowWriter);
                if (ptr == nullptr)
                {
                    return failed("no flow_writer handle named " + writerName);
                }

                mxlStatus const status = 
                    mxlReleaseFlowWriter(instance, static_cast<mxlFlowWriter>(ptr));
                
                json result = statusJson("mxlReleaseFlowWriter", status);
                result["released"] = writerName;
                return result;
        }});

        // ABI call mxlFlowWriterOpenGrain
        calls.push_back(CallSpec{
            "mxlFlowWriterOpenGrain", "flow.h",
            "Open the grain at `index` for writing and hand back its info and payload pointer. "
            "A writer tracks one open grain at a time; close it with commitGrain or cancelGrain.",
            {{"writer", "handle", true, "Registry name of the flow writer"},
                {"index", "uint64", true, "Grain index to open"},
                {"store_as", "handle", true, "Registry name to cache the open grain under"}},
            [](Registry& registry, json const& args)
            {
                std::string const writerName = argString(args, "writer");
                std::string const storeAs    = argString(args, "store_as");
                if (storeAs.empty())
                {
                    return failed("argument 'store as' is required");
                }

                std::string error;
                auto const  writer = static_cast<mxlFlowWriter>(
                    handleArg(registry, args, "writer", HandleKind::FlowWriter, error));
                if (writer == nullptr)
                {
                    return failed(error);
                }

                uint64_t index = 0;
                if (!argUint64(args, "index", index))
                {
                    return failed("argument 'index' must be a uint64 (number or decimal string)");
                }

                mxlGrainInfo grain{};
                uint8_t*     payload = nullptr;

                mxlStatus const status = mxlFlowWriterOpenGrain(writer, index, &grain, &payload);

                json result = statusJson("mxlFlowWriterOpenGrain", status);
                if (status != MXL_STATUS_OK)
                {
                    return result;
                }

                HandleEntry entry{HandleKind::Grain, writer,
                    "open on writer " + writerName, grain, payload};
                
                if (!registry.store(storeAs, entry))
                {
                    // The undo is not "release a handle" -- there isn't one. The open
                    // grain lives in the writer, so cancelling is what puts it back.
                    mxlFlowWriterCancelGrain(writer);
                    return failed("handle name '" + storeAs + "' is already in use");
                }

                result["stored_as"]     = storeAs;
                result["index"]         = grain.index;
                result["grain_size"]    = grain.grainSize;
                result["total_slices"]  = grain.totalSlices;
                result["valid_slices"]  = grain.validSlices;
                result["flags"]         = grain.flags;
                return result;
        }});

        // ABI call mxlFlowWriterCommitGrain
        calls.push_back(CallSpec{
            "mxlFlowWriterCommitGrain", "flow.h",
            "Commit the open grain. The writer closes it only when validSlices reaches totalSlices, "
            "so several partial commits against one open grain are legal.",
            {{"writer", "handle", true, "Registry name of the flow writer"},
                {"grain", "handle", true, "Registry name of the grain cached by OpenGrain"},
                {"valid_slices", "uint64", false, "Slices to declare valid; default totalSlices"},
                {"flags", "uint64", false, "Grain flags; default 0. 1 = MXL_GRAIN_FLAG_INVALID"}},
        [](Registry& registry, json const& args)
        {
            std::string const grainName = argString(args, "grain");
            if (grainName.empty())
            {
                return failed("argument 'grain' is required");
            }

            std::string error;
            auto const writer = static_cast<mxlFlowWriter>(
                handleArg(registry, args, "writer", HandleKind::FlowWriter, error));
            if (writer == nullptr)
            {
                return failed(error);
            }

            HandleEntry entry{};
            if (!registry.findEntry(grainName, HandleKind::Grain, entry))
            {
                return failed("no grain handle named " + grainName);
            }

            // The struct OpenGrain produced, index and all -- commit rejects
            // anything whose index is not the writer's currently open one.
            mxlGrainInfo grain = entry.grain;

            uint64_t validSlices = grain.totalSlices;
            if (args.contains("valid_slices") && !argUint64(args, "valid_slices", validSlices))
            {
                return failed("argument 'valid_slices' must be a uint64");
            }
            if (validSlices > grain.totalSlices)
            {
                return failed("valid_slices " + std::to_string(validSlices) +
                              " exceeds totalSlices " + std::to_string(grain.totalSlices));
            }

            uint64_t flags = 0;
            if (args.contains("flags") && !argUint64(args, "flags", flags))
            {
                return failed("argument 'flags' must be a uint64");
            }

            grain.validSlices = static_cast<uint16_t>(validSlices);
            grain.flags       = static_cast<uint32_t>(flags);

            mxlStatus const status = mxlFlowWriterCommitGrain(writer, &grain);

            json result = statusJson("mxlFlowWriterCommitGrain", status);
            if (status != MXL_STATUS_OK)
            {
                return result;
            }

            bool const complete = (grain.validSlices == grain.totalSlices);
            if (complete)
            {
                // The ABI just closed the grain, so the cached slot is dead. This is
                // not atomic with the findEntry above, and does not need to be: two
                // lanes racing one grain both reach the ABI, the loser gets
                // MXL_ERR_INVALID_ARG, and the grain slot owns no resource to leak.
                HandleEntry dropped{};
                registry.takeEntry(grainName, HandleKind::Grain, dropped);
            }

            result["index"]         = grain.index;
            result["valid_slices"]  = grain.validSlices;
            result["total_slices"]  = grain.totalSlices;
            result["flags"]         = grain.flags;
            result["complete"]      = complete;
            return result;
        }});

        // ABI call mxlFlowWriterCancelGrain
        calls.push_back(CallSpec{
            "mxlFlowWriterCancelGrain", "flow.h",
            "Abandon the writer's open grain. Reset the writer's tracked index only -- bytes "
            "already written into the payload stay written.",
            {{"writer", "handle", true, "Registry name of the flow writer"},
                {"grain", "handle", false, "Registry name of the cached grain to drop as well"}},
            [](Registry& registry, json const& args)
            {
                std::string error;
                auto const  writer = static_cast<mxlFlowWriter>(
                    handleArg(registry, args, "writer", HandleKind::FlowWriter, error));
                if (writer == nullptr)
                {
                    return failed(error);
                }

                mxlStatus const status = mxlFlowWriterCancelGrain(writer);

                json result = statusJson("mxlFlowWriterCancelGrain", status);
                if (status != MXL_STATUS_OK)
                {
                    return result;
                }

                // The ABI forgot the grain; drop the tool's cached copy too, or the two
                // disagree and the next commit fails with a confusing MXL_ERR_INVALID_ARGS.
                std::string const grainName = argString(args, "grain");
                if (!grainName.empty())
                {
                    HandleEntry dropped{};
                    result["dropped"] = registry.takeEntry(grainName, HandleKind::Grain, dropped);
                }
                return result;
        }});

        // ABI call mxlFlowWriterGetGrainInfo
        calls.push_back(CallSpec{
            "mxlFlowWriterGetGrainInfo", "flow.h",
            "Peek at the header of the ring slot an index map to. Does not open the grain, and "
            "does not validate the index -- the slot may hold an entirely different grain.",
            {{"writer", "handle", true, "Registry name of the flow writer"},
                {"index", "uint64", true, "Grain index to look up"}},
            [](Registry& registry, json const& args)
            {
                std::string error;
                auto const  writer = static_cast<mxlFlowWriter>(
                    handleArg(registry, args, "writer", HandleKind::FlowWriter, error));
                if (writer == nullptr)
                {
                    return failed(error);
                }

                uint64_t index = 0;
                if (!argUint64(args, "index", index))
                {
                    return failed("argument 'index' must be a uint64 (number or decimal string)");
                }

                mxlGrainInfo grain{};
                mxlStatus const status = mxlFlowWriterGetGrainInfo(writer, index, &grain);

                json result = statusJson("mxlFlowWriterGetGrainInfo", status);
                if (status != MXL_STATUS_OK)
                {
                    return result;
                }

                // The call cannot fail on a bad index -- it hands back slot index % grainCount
                // whatever lives there. Comparing the two is the only staleness signal there is.
                result["requested_index"] = index;
                result["index"]           = grain.index;
                result["matches"]         = (grain.index == index);
                result["grain_size"]      = grain.grainSize;
                result["total_slices"]    = grain.totalSlices;
                result["valid_slices"]    = grain.validSlices;
                result["flags"]           = grain.flags;
                return result;
        }});

        // ABI call mxlCreateFlowReader
        calls.push_back(CallSpec{
            "mxlCreateFlowReader", "flow.h",
            "Attach a reader to an existing flow, by flow id. A reader never creates a flow.",
            {{"instance", "handle", true, "Registry name of the instance"},
                {"flow_id", "string", true, "UUID of the flow to attach to"},
                {"options", "string", false, "Reserved by the SDK; currently unused"},
                {"store_as", "handle", true, "Registry name to store the new reader under"}},
            [](Registry& registry, json const& args)
            {
                std::string const flowId       = argString(args, "flow_id");
                std::string const storeAs      = argString(args, "store_as");
                if (flowId.empty() || storeAs.empty())
                {
                    return failed("arguments 'flow_id' and 'store_as' are required");
                }

                std::string error;
                auto const  instance = static_cast<mxlInstance>(
                    handleArg(registry, args, "instance", HandleKind::Instance, error));
                if (instance == nullptr)
                {
                    return failed(error);
                }

                std::string const options = argString(args, "options");

                mxlFlowReader   reader{};
                mxlStatus const status = mxlCreateFlowReader(instance, flowId.c_str(),
                    options.empty() ? nullptr : options.c_str(), &reader);
                
                json result = statusJson("mxlCreateFlowReader", status);
                if (status != MXL_STATUS_OK)
                {
                    return result;
                }

                if (!registry.store(storeAs, HandleKind::FlowReader, reader, flowId))
                {
                    mxlReleaseFlowReader(instance, reader);
                    return failed("handle name '" + storeAs + "' is already in use");
                }
                result["stored_as"] = storeAs;
                result["flow_id"]   = flowId;
                return result;
        }});

        // ABI call mxlReleaseFlowReader
        calls.push_back(CallSpec{
            "mxlReleaseFlowReader", "flow.h",
            "Release a flow reader.",
            {{"instance", "handle", true, "Registry name of the instance that owns the reader"},
                {"reader", "handle", true, "Registry name of the reader to release"}},
            [](Registry& registry, json const& args)
            {
                std::string const readerName   = argString(args, "reader");
                if (readerName.empty())
                {
                    return failed("argument 'reader' is required");
                }
                std::string error;
                auto const  instance = static_cast<mxlInstance>(
                    handleArg(registry, args, "instance", HandleKind::Instance, error));
                if (instance == nullptr)
                {
                    return failed(error);
                }

                void* const ptr = registry.take(readerName, HandleKind::FlowReader);
                if (ptr == nullptr)
                {
                    return failed("no flow_reader handle named " + readerName);
                }

                mxlStatus const status =
                    mxlReleaseFlowReader(instance, static_cast<mxlFlowReader>(ptr));
                
                json result = statusJson("mxlReleaseFlowReader", status);
                result["released"] = readerName;
                return result;
        }});
        
        // ABI call mxlIsFlowActive
        calls.push_back(CallSpec{
            "mxlIsFlowActive", "flow.h",
            "Whether the flow currently has an active writer. Reader do not count.",
            {{"instance", "handle", true, "Registry name of the instance"},
                {"flow_id", "string", true, "UUID of the flow to test"}},
            [](Registry& registry, json const& args)
            {
                std::string const flowId       = argString(args, "flow_id");
                if (flowId.empty())
                {
                    return failed("arguments 'flow_id' is required");
                }

                std::string error;
                auto const  instance = static_cast<mxlInstance>(
                    handleArg(registry, args, "instance", HandleKind::Instance, error));
                if (instance == nullptr)
                {
                    return failed(error);
                }

                bool            isActive = false;
                mxlStatus const status   = mxlIsFlowActive(instance, flowId.c_str(), &isActive);

                json result = statusJson("mxlIsFlowActive", status);
                if (status == MXL_STATUS_OK)
                {
                    result["is_active"] = isActive;
                }
                return result;
        }});

        // ABI call mxlGetCurrentIndex
        calls.push_back(CallSpec{
            "mxlGetCurrentIndex", "time.h",
            "The grain index for 'now' at the given edit rate. Rounds to nearest, so it can name a slot that has not started yet.",
            {{"edit_rate", "rational", true, "Grain rate, e.g. {\"num\":30000,\"den\":1001}"}},
            [](Registry&, json const& args)
            {
                mxlRational editRate{};
                if (!argRational(args, "edit_rate", editRate))
                {
                    return failed("argument 'edit_rate' must be {\"num\":<int>,\"den\":<non-zero int>}");
                }

                uint64_t const index = mxlGetCurrentIndex(&editRate);
                uint64_t const nowNs = mxlGetTime();
                uint64_t const otsNs = mxlIndexToTimestamp(&editRate, index);

                return json{{"ok", true},
                            {"index", index},
                            {"ots_ns", nsText(otsNs)},
                            {"now_ns", nsText(nowNs)},
                            {"age_ms", (int64_t)(nowNs - otsNs) / 1000000.0}};
        }});

        // ABI call mxlIndexToTimestamp
        calls.push_back(CallSpec{
            "mxlIndexToTimestamp", "time.h",
            "The OTS in nanoseconds at which the given grain index starts.",
            {{"edit_rate", "rational", true, "Grain rate, e.g. {\"num\":30000,\"den\":1001}"},
                {"index", "uint64", true, "Grain index to convert"}},
            [](Registry&, json const& args)
            {
                mxlRational editRate{};
                if (!argRational(args, "edit_rate", editRate))
                {
                    return failed("argument 'edit_rate' must be {\"num\":<int>,\"den\":<non-zero int>}");
                }

                uint64_t index = 0;
                if (!argUint64(args, "index", index))
                {
                    return failed("argument 'index' must be an unsigned integer or a decimal string");
                }

                return json{{"ok", true},
                            {"index", index},
                            {"ots_ns", nsText(mxlIndexToTimestamp(&editRate, index))}};
        }});

        // ABI call mxlTimestampToIndex
        calls.push_back(CallSpec{
            "mxlTimestampToIndex", "time.h",
            "The grain index for a TAI timestamp. Rounds to nearest, so it is not the inverse of mxlIndexToTimestamp.",
            {{"edit_rate", "rational", true, "Grain rate e.g. {\"num\":30000,\"den\":1001}"},
                {"timestamp_ns", "uint64", true, "TAI ns since the ST 2059 epoch"}},
            [](Registry&, json const& args)
            {
                mxlRational editRate{};
                if (!argRational(args, "edit_rate", editRate))
                {
                    return failed("argument 'edit_rate' must be {\"num\":<int>,\"den\":<non-zero int>}");
                }

                uint64_t timestampNs = 0;
                if (!argUint64(args, "timestamp_ns", timestampNs))
                {
                    return failed("argument 'timestamp_ns' must be an unsigned integer or a decimal string");
                }

                uint64_t const index = mxlTimestampToIndex(&editRate, timestampNs);

                return json{{"ok", true},
                            {"timestamp_ns", nsText(timestampNs)},
                            {"index", index},
                            {"ots_ns", nsText(mxlIndexToTimestamp(&editRate, index))}};
        }});

        // ABI call mxlGetNsUntilIndex
        calls.push_back(CallSpec{
            "mxlGetNsUntilIndex", "time.h",
            "Nanoseconds until the given grain index starts. Zero if that index has already passed.",
            {{"edit_rate", "rational", true, "Grain rate, e.g. {\"num\":30000,\"den\":1001}"},
                {"index", "uint64", true, "Grain index to wait for"}},
            [](Registry&, json const& args)
            {
                mxlRational editRate{};
                if (!argRational(args, "edit_rate", editRate))
                {
                    return failed("argument 'edit_rate' must be {\"num\":<int>,\"den\":<non-zero int>}");
                }

                uint64_t index = 0;
                if (!argUint64(args, "index", index))
                {
                    return failed("argument 'index' must be an unsigned integer or a decimal string");
                }

                // No status code: MXL_UNDEFINED_INDEX (UINT64_MAX) *is* the error report.
                uint64_t const waitNs = mxlGetNsUntilIndex(index, &editRate);
                if (waitNs == MXL_UNDEFINED_INDEX)
                {
                    return failed("mxlGetNsUntilIndex rejected the edit rate");
                }

                return json{{"ok", true},
                            {"index", index},
                            {"wait_ns", nsText(waitNs)},
                            {"wait_ms", waitNs / 1000000.0}};
        }});

        // ABI call mxlSleepForNs
        calls.push_back(CallSpec{
            "mxlSleepForNs", "time.h",
            "Sleep for a duration on the TAI clock. Returns nothing -- the useful number is how much longer it actually slept.",
            {{"ns", "uint64", true, "How long to sleep, in nanoseconds (5s maximum)"}},
            [](Registry&, json const& args)
            {
                uint64_t ns = 0;
                if (!argUint64(args, "ns", ns))
                {
                    return failed("argument 'ns' must be an unsigned integer or a decimal string");
                }
                if (ns > kMaxSleepNs)
                {
                    return failed("argument 'ns' exceeds the " + nsText(kMaxSleepNs) + " ns cap");
                }

                // Nothing to observe but the clock: bracket the call and report the overshoot.
                uint64_t const startNs = mxlGetTime();
                mxlSleepForNs(ns);
                uint64_t const endNs = mxlGetTime();

                uint64_t const actualNs = endNs - startNs;
                return json{{"ok", true},
                            {"requested_ns", nsText(ns)},
                            {"actual_ns", nsText(actualNs)},
                            {"overshoot_us", (int64_t)(actualNs - ns) / 1000.0}};
        }});

        // ABI call mxlSleepUntil
        calls.push_back(CallSpec{
            "mxlSleepUntil", "time.h",
            "Sleep until an absolute TAI timestamp. Returns immediately if that moment has already passed.",
            {{"timestamp_ns", "uint64", true, "TAI ns since the ST 2059 epoch, at most 5s ahead"}},
            [](Registry&, json const& args)
            {
                uint64_t targetNs = 0;
                if (!argUint64(args, "timestamp_ns", targetNs))
                {
                    return failed("argument 'timestamp_ns' must be an unsigned integer or a decimal string");
                }

                uint64_t const startNs = mxlGetTime();

                // The > test guards the substraction: on a past target it would wrap.
                if ((targetNs > startNs) && ((targetNs - startNs) > kMaxSleepNs))
                {
                    return failed("argument 'timestamp_ns' is more than " + nsText(kMaxSleepNs) +
                                  " ns in the future");
                }

                mxlSleepUntil(targetNs);
                uint64_t const endNs = mxlGetTime();

                return json{{"ok", true},
                            {"timestamp_ns", nsText(targetNs)},
                            {"woke_ns", nsText(endNs)},
                            {"slept_ms", (int64_t)(endNs - startNs) / 1000000.0},
                            {"late_us", (int64_t(endNs - targetNs) / 1000.0)}};
        }});

        // ABI call mxlGarbageCollectFlows
        calls.push_back(CallSpec{
            "mxlGarbageCollectFlows", "mxl.h",
            "Delete every flow in the domain that no longer has a reader or writer holding its lock -- the debris left by a crashed process.",
            {{"instance", "handle", true, "Registry name of the instance"}},
            [](Registry& registry, json const& args)
            {
                std::string error;
                auto const  instance = static_cast<mxlInstance>(
                    handleArg(registry, args, "instance", HandleKind::Instance, error));
                if (instance == nullptr)
                {
                    return failed(error);
                }

                return statusJson("mxlGarbageCollectFlows", mxlGarbageCollectFlows(instance));
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