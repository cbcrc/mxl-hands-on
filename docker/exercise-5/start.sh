#!/bin/bash
mkdir -p /Volumes/mxl/domain_1
cp ./data/domain_def.json /Volumes/mxl/domain_1
docker compose up -d
docker container ls