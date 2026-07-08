#!/bin/bash
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
cp ~/mxl-hands-on/build-images/sizzle.ts ./data/Clips
sudo tar -xf ./data/spx-server/spx-server.tar.xz -C ./data/spx-server --skip-old-files
docker compose -f docker-compose.yml -f docker-compose.mac.yml up -d
docker container ls