mkdir -p /Volumes/mxl/domain_1
cp ./data/domain_def.json /Volumes/mxl/domain_1
docker compose -f docker-compose.yml -f docker-compose.mac.yml up -d
docker container ls