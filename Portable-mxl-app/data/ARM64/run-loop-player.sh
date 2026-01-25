#!/bin/bash
# Get the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

# Set the plugin path automatically
export GST_PLUGIN_PATH="$DIR"

# Run the player with whatever arguments you provide
"$DIR/mxl-gst-looping-filesrc" "$@"