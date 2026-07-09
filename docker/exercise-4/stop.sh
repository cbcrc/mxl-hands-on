#!/bin/bash
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
rm ./data/Clips/sizzle.ts
rm -rf ./data/spx-server/ASSETS
rm -rf ./data/spx-server/bin
rm -rf ./data/spx-server/DATAROOT
rm -rf ./data/spx-server/locales
rm -rf ./data/spx-server/LOG
docker compose down
docker container ls