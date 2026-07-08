#!/bin/bash
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
# Entrypoint for the MXL Input Selector container.
# The native Rust backend serves both the REST API and the built React frontend
# on port 9600.

set -e

exec /app/input-selector-router
