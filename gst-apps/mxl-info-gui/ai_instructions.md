# AI Agent Instructions: MXL Info GUI

## Project Overview
Build a Dockerized MXL probing application that uses the `mxl-info` CLI command to display status information on MXL flows present in multiple MXL domains. The application uses a custom-built CLI command named `mxl-info`, FastAPI for the backend API, and React + Vite for the web frontend. Keep code style consistent with `./gst-apps/test-generator`.

## System Architecture
The application runs as a **single service on one port** inside Docker:
1. **FastAPI (port 9600):** Serves both the REST API (`/scan-domain`, etc.) and the built React static files (mounted via `StaticFiles`). The `StaticFiles` mount is added **last**, after all API routes, so API paths take precedence.
2. **Frontend:** Built with React + Vite into a `dist/` directory. No separate static file server — FastAPI serves `dist/` directly via `fastapi.staticfiles.StaticFiles`.

This single-port design means the browser always uses the **same origin** for both the UI and the API, so no port number is hardcoded in the frontend JavaScript. The docker-compose host-port mapping (`9699:9600`) can be changed freely without rebuilding the image.

## Environment & File Specifications
- **MXL-info binary:** The executable lives at `/opt/mxl/tools/mxl-info/mxl-info` inside the container.
  > ⚠️ In the build tree the tool path is `./dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info` — the directory and the executable share the same name.
- **MXL-info source:** `./dmf-mxl/tools/mxl-info/main.cpp` (authoritative reference for output format)
- **MXL domain root:** `/mxl-domain` (mounted Docker volume)

## Backend Functionalities

### 1. `get_domains`
Scans `/mxl-domain` recursively for files named `domain_def.json`. Each file contains the domain UUID:
```json
{"id": "51ef9b5c-98c1-4f98-9def-1d61ee9a4fdb"}
```
For each file found, store:
- The domain **UUID** (from the JSON `id` field)
- The **directory path** containing `domain_def.json` (passed to `mxl-info -d`)
- The **buffer depth** (see below)

This function runs **once at startup** and can be manually re-triggered via API.

#### Buffer depth (`options.json`)
After reading `domain_def.json`, look for an `options.json` file in the **same directory**:

```json
{"urn:x-mxl:option:history_duration/v1.0": 500000000}
```

The value is expressed in **nanoseconds**. Convert to milliseconds for display (`ns / 1_000_000`).

- If `options.json` is **absent**, the key is **missing**, or the file is **unreadable** → use the default of **200 ms**.
- Store two fields per domain:
  - `buffer_depth_ms` — `float`, milliseconds (e.g. `500.0` or `200.0`)
  - `buffer_depth_is_default` — `bool`, `True` when falling back to the default

Constants used in the implementation:
```python
_HISTORY_DURATION_KEY    = "urn:x-mxl:option:history_duration/v1.0"
_DEFAULT_BUFFER_DEPTH_MS = 200.0
```

### 2. `scan_domain`
Calls `mxl-info -d <domain_directory>` and parses the output into a list of flows.

**Actual CLI output format (non-terminal / subprocess):**
```
<GroupName>: mxl:///<domain>?id=<uuid1>&id=<uuid2>...
	<RoleInGroup> : <UUID> - <Label>
	<RoleInGroup> : <UUID> - <Label>

<GroupName2>: mxl:///<domain>?id=<uuid>...
	<RoleInGroup> : <UUID> - <Label>
```

**Edge cases (from source code `main.cpp`):**
- When `groupName` is empty (no grouphint group), the header reads: `Invalid group name (empty string): mxl://...`
- When `roleInGroup` is empty, the role column shows `MISSING ROLE`
- Groups are sorted alphabetically by group name

**Parsing rules:**
- Lines starting with whitespace are flow lines: `\t<RoleInGroup> : <UUID> - <Label>`
- Lines without leading whitespace are group headers; extract the group name as text before the first `:`
- Detect `Invalid group name (empty string)` header and treat the current group as `""` (empty)
- Normalize `MISSING ROLE` role to empty string
- Reconstruct `flow_grouphint` as `"<GroupName>:<RoleInGroup>"` when both are present; fall back to whichever is non-empty
- Use a UUID-anchored regex to distinguish flow lines from URL lines: `r'^\s+(.+?)\s*:\s*([0-9a-f]{8}-[0-9a-f]{4}-...)\s*-\s*(.+)$'`

**Parsed JSON output per flow:**
```json
{
  "flow_uuid":      "28cc59be-3546-515f-8326-fc5639e8a7f0",
  "flow_label":     "HTML5 Keyer – Output",
  "flow_grouphint": "Test-Generator:Video",
  "description":    "video-out-1"
}
```
After parsing the `mxl-info -d` output, each flow is enriched with `description` read directly from `<domain_path>/<flow_uuid>.mxl-flow/flow_def.json` (the `description` field). Returns `""` if the file is absent or unreadable.

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
- Skip the `- Flow [...]` header line
- For all other non-empty lines, split on the first `:` to extract key/value pairs, stripping whitespace
- Return as `{"flow_uuid": "<uuid>", "fields": {"Version": "1", ...}}`

### 4. `classify_domain_flows`
Classifies a domain's flows into an **active** list and an **inactive/stale** list. This is the single source of truth for both the live flow table and the Inactive/Stale Flows table — the per-flow activity check runs once per scan, not twice.

A flow being reported by `mxl-info -d` is **not** sufficient to call it active: `mxl-info -d` can list a flow whose underlying buffer is no longer being written. Activity must be confirmed per flow via `mxl-info -d <domain> -f <flow_uuid>`, reading the trailing `Active: true/false` line.

**Algorithm:**
1. Call `mxl-info -d <domain>` and parse the reported flows (see `scan_domain`); collect their UUIDs.
2. For each reported flow, run `mxl-info -d <domain> -f <flow_uuid>` and read the `Active` field:
   - `Active: true` → **active** list (enriched with `description` from `flow_def.json`).
   - otherwise → inactive list, tagged `status: "inactive"`, with `directory` added.
   - Any failure to confirm activity (non-zero exit, timeout, missing `Active` line) is treated as **not active**: if a flow can't be confirmed active it doesn't belong in the live list.
3. Iterate the domain directory for `<uuid>.mxl-flow/` entries whose UUID is **not** reported by `mxl-info -d`; read `flow_def.json` for `label`, `description`, and the grouphint tag `urn:x-nmos:tag:grouphint/v1.0`; append to the inactive list tagged `status: "stale"`.
4. Return `{"active": [...], "inactive": [...]}`. Active flow fields: `flow_uuid`, `flow_label`, `flow_grouphint`, `description`. Inactive flow fields add `directory` and `status` (`"inactive"` or `"stale"`).

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/get-domains` | Trigger a fresh domain scan; returns updated domain list |
| `GET`  | `/domains` | Return the cached domain list |
| `GET`  | `/domain-flows?domain_path=<path>` | Classify flows into `{active, inactive}` (active confirmed via per-flow `Active` check; inactive list = `inactive` + `stale` statuses) |
| `GET`  | `/flow-info?domain_path=<path>&flow_uuid=<uuid>` | Run `mxl-info -d -f` and return flow fields |

## Required UI Functionalities

1. **Scan Domain button** — calls `POST /get-domains`. Domains are also polled every **30 seconds** via `GET /domains`.
2. **Domain List window** — table showing UUID, directory path, and **Buffer Depth** for each domain found. The buffer depth is read from `options.json` in the domain directory (`urn:x-mxl:option:history_duration/v1.0`, expressed in nanoseconds, displayed in ms). When no `options.json` is present the value shows as `200 ms` with a dimmed `(default)` annotation.
3. **Domain Selector** — dropdown to select a domain from the discovered list.
4. **MXL Flow List window** — table showing `Flow UUID`, `Label`, `Description`, and `Group Hint` for the **active** flows in the selected domain (those confirmed `Active: true`). Flows are **grouped by group name** (the prefix before `:` in `flow_grouphint`); each group is shown with a coloured header row spanning all four columns. Scales up to 20 rows; scrollable beyond that. Polls `/domain-flows` every **30 seconds** when a domain is selected.
5. **Refresh Flow List button** — manually triggers `/domain-flows` for the selected domain (refreshes both the active and inactive tables).
6. **Inactive / Stale Flows section** — shown below the flow list when a domain is selected. Displays the `inactive` list from `/domain-flows`. Columns: Status, Flow UUID, Label, Group Hint, Directory. The **Status** column distinguishes `inactive` (reported by `mxl-info -d` but `Active: false`) from `stale` (on-disk `.mxl-flow` directory not reported at all). Populated from the same `/domain-flows` call as the active list, so it refreshes on the same 30 s poll.
7. **Flow 1 Selector** — dropdown populated from the active flow list. Each option displays the flow label, group hint, and the first 8 characters of the UUID in the format `<Label> — <GroupHint> (<UUID prefix>…)`.
8. **Flow 1 Info Display** — shows parsed output of `get_flow_info` for the selected flow. A **"Live update (0.5 s)" checkbox** sits next to the window title. When checked, `get_flow_info` is polled every 500 ms. **Off by default.** Selecting a new domain resets the checkbox to off. A single fetch is always performed immediately when a flow is selected.
9. **Flow 2 Selector** — independent dropdown, same format as Flow 1.
10. **Flow 2 Info Display** — same behaviour as Flow 1 Info Display with its own independent checkbox.

> ⚠️ The frontend must always guard API responses before calling `.map()` on flow lists, to avoid a blank-page crash if the backend returns an error object. For `/domain-flows`, guard each list independently: `Array.isArray(d?.active) ? d.active : []` and `Array.isArray(d?.inactive) ? d.inactive : []`.

## Step-by-Step Implementation Guide

### Step 1: Docker Setup
- The build context is the **repository root** (`..` relative to `./gst-apps/`). All `COPY` paths in the Dockerfile are relative to the repository root (e.g. `COPY gst-apps/mxl-info-gui/backend/ /app/backend/`).
- Use `./gst-apps/docker-compose.yml` as a baseline. The `mxl-domain` named volume is defined there and shared by all services.
- Port mapping: **`9699:9600`** only — FastAPI serves both the API and the React frontend on the same port. No separate frontend port is needed.
- The Dockerfile uses **two stages**: a `node:18-bullseye-slim` stage to build the React frontend, and an `ubuntu:24.04` runtime stage.
- The runtime stage installs only: `python3`, `python3-pip`, and `curl` (no GStreamer needed).
- Build the React frontend in Stage 1; copy the `dist/` output to the runtime image.

**Stage 1 highlights:**
```dockerfile
FROM node:18-bullseye-slim AS frontend-builder
WORKDIR /build
COPY gst-apps/mxl-info-gui/frontend/package.json \
     gst-apps/mxl-info-gui/frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY gst-apps/mxl-info-gui/frontend/ ./
# Logo is copied here so Vite includes it in the dist output
COPY gst-apps/logo/rgb_cbc-radio-canada-col-coul.png ./public/cbc-logo.png
RUN npm run build
```

**Stage 2 highlights:**
```dockerfile
# Copy mxl-info binary (note: directory and executable share the same name)
COPY dmf-mxl/build/Linux-Clang-Release/tools/mxl-info/mxl-info /opt/mxl/tools/mxl-info/mxl-info
RUN chmod +x /opt/mxl/tools/mxl-info/mxl-info

# Copy MXL shared library and create symlinks
COPY dmf-mxl/build/Linux-Clang-Release/lib/libmxl.so.1.1 /opt/mxl/lib/libmxl.so.1.1
RUN cd /opt/mxl/lib \
 && ln -sf libmxl.so.1.1 libmxl.so.1 \
 && ln -sf libmxl.so.1  libmxl.so \
 && ldconfig /opt/mxl/lib

ENV LD_LIBRARY_PATH=/opt/mxl/lib
EXPOSE 9600
```

### Step 2: FastAPI Backend
- Create a FastAPI application at `backend/main.py`.
- `MXL_INFO_BIN = "/opt/mxl/tools/mxl-info/mxl-info"`
- `MXL_DOMAIN_ROOT = os.environ.get("MXL_DOMAIN", "/mxl-domain")`
- Use `subprocess.run([MXL_INFO_BIN, ...], capture_output=True, text=True, timeout=10)`.
- Implement the five endpoints listed above.
- Call `get_domains()` in the FastAPI `startup` event.
- Use a UUID-anchored regex for flow line parsing to reliably distinguish flow lines from header lines (which also contain `:` in the mxl:// URL).

### Step 3: React + Vite Frontend
- Initialize with `package.json` targeting Vite 5 and React 18.
- Dev proxy in `vite.config.js` pointing all API paths to `http://localhost:9600`. Set the Vite dev port to the app's docker-compose host port + 100 (convention across all gst-apps: host 9699 → dev 9799).
- Dark theme consistent with `./gst-apps/test-generator` (background `#0f0f0f`, section cards `#1c1c1c`).
- **Header branding:** Display the CBC Radio-Canada logo (`/cbc-logo.png`) inline beside the "MXL Info GUI" h1 title in a flex row (`display: flex; align-items: center; gap: 1rem`). The logo has `height: 2.2rem`.
- Flow list table uses group header rows (coloured, spanning all columns) to visually group flows by their group name (prefix before `:` in `flow_grouphint`).
- Orphan flows section appears below the active flow list whenever a domain is selected.
- Flow 1 and Flow 2 panels in a two-column grid, each managing their own state independently.
- API base: `const API = ""` — all fetch calls use relative paths (e.g. `${API}/scan-domain`), so they hit the same origin as the page. No port number is hardcoded in the frontend.

### Step 4: Entrypoint
FastAPI serves everything on a single port — no separate static file server needed.
```bash
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 9600
```

The `StaticFiles` mount in `backend/main.py` must be **last** (after all `@app.get`/`@app.post` routes) so API routes take precedence over the catch-all `html=True` handler:
```python
app.mount("/", StaticFiles(directory="/app/frontend/dist", html=True), name="static")
```
`aiofiles` must be in `requirements.txt` — FastAPI's `StaticFiles` depends on it.

Please write or modify the Dockerfile, Python backend, React components, and entrypoint at `./gst-apps/mxl-info-gui/` following these guidelines.
