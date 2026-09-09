// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <string>
#include <vector>
#include <nlohmann/json.hpp>

// Where the .json files live: SCENARIO_DIR, or ../scenarios relative to the
// directory the server was launched from. M16's Dockerfile sets /app/scenarios.
std::string scenarioDir();

// The stem of every *.json in that directory, sorted. Never throws -- a missing
// directory is an empty list warning, the same contract as scanDomains.
std::vector<std::string> listScenarios();

// A name is a bare stem: letters, digits, '-' and '_'. No dots at all, which is
// what makes "../.." unrepresentable rather than merely filtered out.
bool validScenarioName(std::string const& name, std::string& error);

// Read <dir>/<name>.json. false + error on a bad name, a missing file or text
// that parses but is not a JSON object.
bool readScenario(std::string const& name, nlohmann::json& out, std::string& error);

// Write <dir>/<name>.json, pretty-printed, creating the directory if needed.
bool writeScenario(std::string const& name, nlohmann::json const& doc, std::string& error);