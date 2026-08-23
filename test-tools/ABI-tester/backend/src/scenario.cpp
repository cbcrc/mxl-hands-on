// SPDX-FilecopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#include "scenario.hpp"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>

namespace fs = std::filesystem;

std::string scenarioDir()
{
    char const* fromEnv = std::getenv("SCENARIO_DIR");
    return (fromEnv != nullptr) ? fromEnv : "../scenarios";
}

std::vector<std::string> listScenarios()
{
    std::string const dir = scenarioDir();

    std::vector<std::string> names;
    std::error_code          ec;

    // The non-throwing overload: on a missing directory the iterator compares
    // equal to end() straight away, so the body never runs and ec carries the
    // reason. That is why the check below sits after the loop, not before it.
    for (auto const& entry : fs::directory_iterator(dir, ec))
    {
        if (entry.path().extension() == ".json")
        {
            names.push_back(entry.path().stem().string());
        }
    }

    if (ec)
    {
        std::fprintf(stderr, "scenario dir %s: %s\n", dir.c_str(), ec.message().c_str());
    }

    std::sort(names.begin(), names.end());
    return names;
}

bool validScenarioName(std::string const&name, std::string& error)
{
    if (name.empty() || (name.size() > 64))
    {
        error = "scenario name must be 1 to 64 characters";
    }

    for (char const c : name)
    {
        bool const allowed = ((c >= 'a') && (c <= 'z')) || ((c >= 'A') && (c <= 'Z')) ||
                             ((c >= '0') && (c <= '9')) || (c == '-') || (c == '_');
        if (!allowed)
        {
            error = "scenario name may contain only letters, digits, '-' and '_'";
            return false;
        }
    }

    return true;
}

bool readScenario(std::string const& name, nlohmann::json& out, std::string& error)
{
    if (!validScenarioName(name, error))
    {
        return false;
    }

    // operator / joins path components, exactly like pathlib's. Because the name
    // holds no separator, the join cannot leave the directory.
    fs::path const path = fs::path(scenarioDir()) / (name + ".json");

    std::ifstream file(path);
    if (!file)
    {
        error = "no scenario named " + name;
        return false;
    }

    out = nlohmann::json::parse(file, nullptr, false);
    if (out.is_discarded() || !out.is_object())
    {
        error = name + ".json is not a JSON object";
        return false;
    }

    return true;
}

bool writeScenario(std::string const& name, nlohmann::json const& doc, std::string& error)
{
    if (!validScenarioName(name, error))
    {
        return false;
    }

    if (!doc.is_object())
    {
        error = "a scenario mus be a JSON object";
        return false;
    }

    std::error_code ec;
    fs::create_directories(scenarioDir(), ec);

    fs::path const path = fs::path(scenarioDir()) / (name + ".json");

    std::ofstream file(path);
    file << doc.dump(2) << "\n";
    if (!file)
    {
        error = "could not write " + path.string();
        return false;
    }

    return true;
}