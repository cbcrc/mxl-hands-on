# GitHub Copilot Agent Instructions: MXL-INFO web GUI (v2)

## Project Overview
Your task is to build a Dockerized MXL probing application that uses the `mxl-info` CLI command to display status information on MXL flows present in multiple MXL domains. The application uses a custom-built CLI command named `mxl-info`, FastAPI for the backend API, and React + Vite for the web frontend. Other applications using the same Python/React+Vite stack can be found in `./docker/exercise-5/data`. Keep code style consistent with those applications.

## System Architecture
The application consists of two main components running within a Docker environment:
1. **Frontend:** A web UI built with React and Vite, served as static files on port 9760.
2. **Backend / CLI Engine:** A Python application using FastAPI on port 9660 that calls the `mxl-info` CLI via `subprocess` and parses its output to JSON for the frontend.

## Environment & File Specifications
- **MXL-info binary:** The binary is located at `./dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info`.
  > ⚠️ `mxl-info` is a **build directory**, not a file. The executable itself is the nested `mxl-info/mxl-info` path. In the Docker container it lives at `/opt/mxl/tools/mxl-info/mxl-info`.
- **MXL-info usage docs:** `./dmf-mxl/docs/tools.md`
- **MXL domain root:** `/mxl-domain` (mounted Docker volume)

## Backend Functionalities
The Python backend must implement the following:

### 1. `get_domains`
Scans `/mxl-domain` recursively for files named `domain_def.json`. Each file contains the domain UUID:
```json
{"id": "51ef9b5c-98c1-4f98-9def-1d61ee9a4fdb"}
```
For each file found, store:
- The domain **UUID** (from the JSON `id` field)
- The **directory path** containing `domain_def.json` (this directory is passed to `mxl-info -d`)

This function runs **once at startup** and can be manually re-triggered via API.

### 2. `scan_domain`
Calls `mxl-info -d <domain_directory>` and parses the output into a list of flows.

**Actual CLI output format:**
```
Media Function 1: mxl:///mxl-domain?id=28cc59be...&id=6dd5a229...
	Video : 28cc59be-3546-515f-8326-fc5639e8a7f0 - HTML5 Keyer – Output
	Video : 6dd5a229-f9fc-5445-b748-18df3ff391cc - Test Generator – Video
	Audio : b1fc38a5-7cb3-5b23-ba6c-c3b73d3920da - Test Generator – Audio
	Video : 0a6fbc49-f73c-5f75-a588-9c10ea83f98b - Input Selector – Output
```

**Parsing rules:**
- Lines that do **not** start with whitespace are group headers. Extract the group name as everything before the first `:` (e.g. `"Media Function 1"`).
- Lines starting with a tab or spaces are flow lines in the format `<Format> : <UUID> - <Label>`.
- The `flow_grouphint` is composed as `"<GroupName>:<Format>"` (e.g. `"Media Function 1:Video"`).

**Parsed JSON output per flow:**
```json
{
  "flow_uuid":      "28cc59be-3546-515f-8326-fc5639e8a7f0",
  "flow_label":     "HTML5 Keyer – Output",
  "flow_grouphint": "Media Function 1:Video"
}
```

### 3. `get_flow_info`
Calls `mxl-info -d <domain_directory> -f <flow_uuid>` and parses the output.

**Actual CLI output format:**
```
- Flow [6dd5a229-f9fc-5445-b748-18df3ff391cc]
	             Version: 1
	         Struct size: 2048
	              Format: Video
	   Grain/sample rate: 60/1
	   Commit batch size: 1080
	     Sync batch size: 1080
	    Payload Location: Host
	        Device Index: -1
	               Flags: 00000000
	         Grain count: 12

	          Head index: 106730821602
	     Last write time: 1778847026700056836
	      Last read time: 1778846943497899828
	Latency (grains, ms): 1, 14.036221
	              Active: true
```

**Parsing rules:**
- Skip the `- Flow [...]` header line.
- For all other non-empty lines, split on the first `:` to extract key/value pairs, stripping surrounding whitespace from both.
- Return as `{"flow_uuid": "<uuid>", "fields": {"Version": "1", ...}}`.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/get-domains` | Trigger a fresh domain scan; returns updated domain list |
| `GET`  | `/domains` | Return the cached domain list |
| `GET`  | `/scan-domain?domain_path=<path>` | Run `mxl-info -d` and return flow list |
| `GET`  | `/flow-info?domain_path=<path>&flow_uuid=<uuid>` | Run `mxl-info -d -f` and return flow fields |

## Required UI Functionalities

1. **Scan Domain button** — calls `POST /get-domains`. Domains are also polled every **30 seconds** via `GET /domains`.
2. **Domain List window** — table showing UUID and directory path for each domain found. Scales automatically with the number of domains.
3. **Domain Selector** — dropdown to select a domain from the discovered list.
4. **MXL Flow List window** — table showing `FlowUUID`, `Flow Label`, and `Flow Grouphint` for all flows in the selected domain. Scales up to 20 rows; scrollable beyond that. Polls `scan_domain` every **30 seconds** when a domain is selected.
5. **Refresh Flow List button** — manually triggers `scan_domain` for the selected domain.
6. **Flow 1 Selector** — dropdown populated from the flow list.
7. **Flow 1 Info Display** — shows parsed output of `get_flow_info` for the selected flow. Polls every **500 ms**.
8. **Flow 2 Selector** — independent dropdown populated from the same flow list.
9. **Flow 2 Info Display** — shows parsed output of `get_flow_info` for the second selected flow. Polls every **500 ms**.

> ⚠️ The frontend must always guard API responses with `Array.isArray(d) ? d : []` before calling `.map()` on flow lists, to avoid a blank-page crash if the backend returns an error object.

## Step-by-Step Implementation Guide

### Step 1: Docker Setup
- Use `./docker/exercise-5/docker-compose-dev.yml` as a baseline and add the `mxl-info-gui` service.
- **No NMOS node** is needed for this application.
- Base image: `ubuntu:24.04`. Install `python3`, `python3-pip`, and `curl` only (no GStreamer).
- Build the React frontend in a `node:18-bullseye-slim` stage; copy the `dist/` output to the runtime image.
- Ports: `9660` (FastAPI backend), `9760` (static frontend).
- Volume: `mxl-domain:/mxl-domain`.

**Dockerfile highlights:**
```dockerfile
# Copy mxl-info binary (note: executable is inside the mxl-info subdirectory)
COPY dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info /opt/mxl/tools/mxl-info/mxl-info
RUN chmod +x /opt/mxl/tools/mxl-info/mxl-info

# Copy MXL shared libraries
COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1 /opt/mxl/lib/libmxl.so.1.1
COPY dmf-mxl/build/Linux-Clang-Release/lib/internal/libmxl-common.so.1.1 /opt/mxl/lib/libmxl-common.so.1.1

ENV LD_LIBRARY_PATH=/opt/mxl/lib
```

### Step 2: FastAPI Backend
- Create a FastAPI application at `backend/main.py`.
- `MXL_INFO_BIN = "/opt/mxl/tools/mxl-info/mxl-info"`
- `MXL_DOMAIN_ROOT = os.environ.get("MXL_DOMAIN", "/mxl-domain")`
- Use `subprocess.run([MXL_INFO_BIN, ...], capture_output=True, text=True, timeout=10)`.
- Implement the four endpoints listed above.
- Call `get_domains()` in the FastAPI `startup` event.

### Step 3: React + Vite Frontend
- Initialize with `package.json` targeting Vite 5 and React 18.
- Dev proxy in `vite.config.js` pointing all API paths to `http://localhost:9660`.
- Dark theme consistent with other apps in `./docker/exercise-5/data` (background `#0f0f0f`, section cards `#1c1c1c`).
- Flow 1 and Flow 2 panels in a two-column grid, each managing their own selected flow UUID and polling interval via `useEffect`.
- API base: `` `http://${window.location.hostname}:9660` ``

### Step 4: Entrypoint
```bash
# Serve React static files on port 9760
python3 -m http.server 9760 --directory /app/frontend/dist &

# Start FastAPI backend on port 9660 (foreground)
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9660
```

Please write the necessary Dockerfiles, Python backend scripts, React components, and integration code following these guidelines at `./docker/exercise-5/data/mxl-info-gui`.
