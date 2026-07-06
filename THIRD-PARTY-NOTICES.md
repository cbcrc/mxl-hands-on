<!--
SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
SPDX-License-Identifier: CC-BY-4.0
-->

# Third-Party Notices

The source code in this repository is licensed as described in [`LICENSES/LICENSE.md`](LICENSES/LICENSE.md) (Apache-2.0 for code, CC-BY-4.0 for documentation and media).

The **container images** built from `gst-apps/` and published under `ghcr.io/cbcrc/` additionally bundle third-party open-source components under their own licenses. This file lists those components, their licenses, and where to obtain their source code.

## Components common to most images

| Component | License | In images | Source |
|---|---|---|---|
| Ubuntu 24.04 base system | Various (see `/usr/share/doc/*/copyright` in the image) | all | [Ubuntu source packages](https://packages.ubuntu.com/noble/) (`apt-get source <package>`) |
| GStreamer core, `-plugins-base`, `-plugins-good`, `-plugins-bad`, `gstreamer1.0-nice` | LGPL-2.1-or-later (libnice: LGPL-2.1 / MPL-1.1) | all GStreamer apps | Ubuntu source packages `gstreamer1.0`, `gst-plugins-base1.0`, `gst-plugins-good1.0`, `gst-plugins-bad1.0`, `libnice` |
| MXL SDK (`libmxl.so`, `libgstmxl.so` mxlsrc/mxlsink, `mxl-info`) | Apache-2.0 | all | [dmf-mxl/mxl](https://github.com/dmf-mxl/mxl) |
| React, Vite-built frontend bundles | MIT | all | [react](https://github.com/facebook/react), [vite](https://github.com/vitejs/vite) |
| Python runtime deps (FastAPI, uvicorn, pydantic, aiofiles, requests, …) | MIT / BSD-3-Clause / Apache-2.0 | Python-backend apps | PyPI |

## Per-image components

| Component | License | In images | Source |
|---|---|---|---|
| gst-plugins-rs `closedcaption` plugin (`libgstrsclosedcaption.so`, built from tag 0.14.5) | MPL-2.0 | **test-generator**, **mxl2webrtc** | [gst-plugins-rs 0.14.5](https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs/-/tree/0.14.5) |
| gstcefsrc GStreamer plugin | **No published license** (see legal checklist below) | **HTML5-keyer** | [centricular/gstcefsrc](https://github.com/centricular/gstcefsrc) |
| CEF (Chromium Embedded Framework) binary distribution + Chromium components | BSD-3-Clause (CEF) + various Chromium licenses; official Spotify CDN builds **exclude** patent-encumbered codecs (H.264, AAC) | **HTML5-keyer** (`/opt/cef`) | [CEF builds](https://cef-builds.spotifycdn.com/index.html); license/credits files are kept inside the image under `/opt/cef` |
| Vosk speech recognition runtime (`vosk` Python package) | Apache-2.0 | **HTML5-keyer** | [alphacep/vosk-api](https://github.com/alphacep/vosk-api) |
| Vosk models `vosk-model-small-en-us-0.15`, `vosk-model-small-fr-0.22` | Apache-2.0 (per [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models)) | **HTML5-keyer** (`/opt/vosk`) | same page |

## Components referenced but **not** redistributed by this project

- **MediaMTX** (MIT) — pulled directly from Docker Hub (`bluenviron/mediamtx`) by users at deploy time.
- **`gstreamer1.0-plugins-ugly`** (GPL; contains the `x264enc` H.264 encoder, which is both GPL-licensed and patent-encumbered) — deliberately **not included** in any published image. The `mxl2webrtc` app requires `x264enc` and installs `gstreamer1.0-plugins-ugly` from the Ubuntu archive **at container start** (see `gst-apps/mxl2webrtc/`). The GPL combined work is therefore created on the deployer's machine and not distributed by this project. Apache-2.0 is one-way compatible with GPL-3.0, so this use is license-compatible either way.
- **`gstreamer1.0-libav` + FFmpeg libraries** (LGPL-2.1-or-later / GPL-2.0-or-later as built by Ubuntu, whose `libavcodec` also hard-depends on the GPL, patent-encumbered `libx264` encoder library) — deliberately **not included** in any published image. The `hls2mxl` and `file-player` apps need the FFmpeg decoders (H.264/AAC) and install `gstreamer1.0-libav` from the Ubuntu archive **at container start**, same pattern as above.

## LGPL corresponding source

The LGPL-licensed binaries in the published images (GStreamer, libnice, and other Ubuntu libraries) are unmodified Ubuntu 24.04 (noble) packages. Corresponding source for any of them can be obtained from the [Ubuntu package archive](https://packages.ubuntu.com/noble/) or with `apt-get source <package>` on an Ubuntu 24.04 system, for the exact package versions recorded in each image (`dpkg -l` inside the image). No GPL-licensed codec packages are included in the published images; they are installed on the deployer's machine at container start where required (see above).

## Per-image SBOMs

This file is a component-level overview. The published images additionally carry a full **SPDX SBOM attestation** (and provenance), generated with syft at build time via `docker buildx build --sbom=true` (see `how_to_build.md`, Step 4). Retrieve any image's SBOM with:

```sh
docker buildx imagetools inspect ghcr.io/cbcrc/<image>:latest --format '{{ json .SBOM }}'
```

## Open items for legal review

This section is a checklist for CBC/Radio-Canada legal/OSPO review; nothing in this file is legal advice.

1. **gstcefsrc has no license file or license headers** in its upstream repository. Redistributing a compiled binary of it (in the HTML5-keyer image) has no explicit license grant. Options: ask upstream (Centricular) to add a license, obtain written permission, or stop redistributing the plugin (build it at deploy time).
