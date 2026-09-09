#!/bin/bash
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
# Entrypoint for the MXL ABI Tester container.
# The native C++ backend serves both the REST API and the built React frontend on port 9600.

set -e

# The binary requires at least one [alias=]<path>; a bare invocation exits 1.
# A compose `command:` overrides this default and can bind several aliases.
if [ "$#" -eq 0 ]; then
    set -- "default=/mxl-domain"
fi

exec /app/abi-tester "$@"