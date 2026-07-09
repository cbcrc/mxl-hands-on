#!/bin/bash
# SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
# SPDX-License-Identifier: Apache-2.0
docker compose -f docker-compose.yml -f docker-compose.mac.yml down
docker container ls