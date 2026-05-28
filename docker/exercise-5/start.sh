#!/bin/bash
mkdir -p /Volumes/mxl/domain_1
cp ./data/domain_def.json /Volumes/mxl/domain_1
sudo tar -xf ./data/spx-server/spx-server.tar.xz -C ./data/spx-server --skip-old-files
docker compose up -d
docker container ls