#!/bin/bash
# SPDX-FileCopyrightText: 2025 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
# Get the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

# Set the plugin path automatically
export GST_PLUGIN_PATH="$DIR"

# Run the player with whatever arguments you provide
"$DIR/mxl-gst-looping-filesrc" "$@"