#!/bin/bash
mkdir -p /Volumes/mxl/domain_1
cp ./data/domain_def.json /Volumes/mxl/domain_1
# To be uncommented when ready to test with the html5 keyer application
# tar -xf ./data/spx-server/spx-server.tar.xz -C ./data/spx-server --skip-old-files
docker compose up -d
docker container ls