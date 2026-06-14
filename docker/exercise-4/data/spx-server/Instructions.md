# SPX Broadcast Docker v1.0.3 — Installation Instructions

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) installed and running
- [Docker Compose](https://docs.docker.com/compose/install/) (included with Docker Desktop)

---

## Installation

### 1. Load the Docker image

From the directory containing this file, run:

```bash
docker load -i spx-gc-broadcast-v1.0.3.tar.gz
```

Verify the image loaded successfully:

```bash
docker images | grep spx-server
```

You should see `spx-server` listed with tag `v1.0.3`.

### 2. Start the container

```bash
docker compose up -d
```

This starts the container in the background using the settings in `docker-compose.yml`.

### 3. Open SPX

Once running, open a browser and navigate to:

```
http://localhost:5660
```

---

## Folder structure

| Folder / File        | Purpose                               |
| -------------------- | ------------------------------------- |
| `ASSETS/`            | Media assets used by templates        |
| `DATAROOT/`          | Projects, rundowns, and template data |
| `locales/`           | UI language files                     |
| `bin/`               | Helper scripts                        |
| `config.docker.json` | Application configuration             |
| `docker-compose.yml` | Docker Compose service definition     |
