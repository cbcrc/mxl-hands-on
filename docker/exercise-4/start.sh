#!/bin/bash
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
mkdir -p /Volumes/mxl/domain_1
cp ./data/domain_def.json /Volumes/mxl/domain_1
cp ~/mxl-hands-on/build-images/sizzle.ts ./data/Clips
sudo tar -xf ./data/spx-server/spx-server.tar.xz -C ./data/spx-server --skip-old-files
docker compose up -d
docker container ls