<!--
SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
SPDX-License-Identifier: CC-BY-4.0
-->

# Third-Party Notices

The source code in this repository is licensed as described in [`LICENSES/LICENSE.md`](LICENSES/LICENSE.md) (Apache-2.0 for code, CC-BY-4.0 for documentation and media).

The **container images** built from `gst-apps/` and `build-images/` (demo images) and published under `ghcr.io/cbcrc/` additionally bundle third-party open-source components under their own licenses. This file lists those components, their licenses, and where to obtain their source code.

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
| gstcefsrc GStreamer plugin | LGPL-2.1-or-later  | **HTML5-keyer** | [centricular/gstcefsrc](https://github.com/centricular/gstcefsrc) |
| CEF (Chromium Embedded Framework) binary distribution + Chromium components | BSD-3-Clause (CEF) + various Chromium licenses; official Spotify CDN builds **exclude** patent-encumbered codecs (H.264, AAC) | **HTML5-keyer** (`/opt/cef`) | [CEF builds](https://cef-builds.spotifycdn.com/index.html); license/credits files are kept inside the image under `/opt/cef` |
| Vosk speech recognition runtime (`vosk` Python package) | Apache-2.0 | **HTML5-keyer** | [alphacep/vosk-api](https://github.com/alphacep/vosk-api) |
| Vosk models `vosk-model-small-en-us-0.15`, `vosk-model-small-fr-0.22` | Apache-2.0 (per [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models)) | **HTML5-keyer** (`/opt/vosk`) | same page |

## Demo images (`build-images/`: mxl-writer, mxl-reader, mxl-clip-player)

| Component | License | In images | Source |
|---|---|---|---|
| Debian 13 (trixie) base system | Various (see `/usr/share/doc/*/copyright` in the image) | all demo images | [Debian source packages](https://packages.debian.org/trixie/) (`apt-get source <package>`) |
| GStreamer core, `-plugins-base`, `-plugins-good`, `-plugins-bad` (**mxl-clip-player** ships only the `mpegtsdemux`, `videoparsersbad` and `audioparsers` plugin files plus `libgstreamer-plugins-bad1.0-0`) | LGPL-2.1-or-later | **mxl-writer**, **mxl-clip-player** | Debian source packages `gstreamer1.0`, `gst-plugins-base1.0`, `gst-plugins-good1.0`, `gst-plugins-bad1.0` |
| MXL SDK (`libmxl.so`, `mxl-gst-testsrc`, `mxl-info`, `mxl-gst-looping-filesrc`, `looping_filesrc` GStreamer plugin) | Apache-2.0 | all demo images | [dmf-mxl/mxl](https://github.com/dmf-mxl/mxl) |

## Components referenced but **not** redistributed by this project

- **MediaMTX** (MIT) — pulled directly from Docker Hub (`bluenviron/mediamtx`) by users at deploy time.
- **`gstreamer1.0-plugins-ugly`** (GPL; contains the `x264enc` H.264 encoder, which is both GPL-licensed and patent-encumbered) — deliberately **not included** in any published image. The `mxl2webrtc` app requires `x264enc` and installs `gstreamer1.0-plugins-ugly` from the Ubuntu archive **at container start** (see `gst-apps/mxl2webrtc/`). The GPL combined work is therefore created on the deployer's machine and not distributed by this project. Apache-2.0 is one-way compatible with GPL-3.0, so this use is license-compatible either way.
- **`gstreamer1.0-libav` + FFmpeg libraries** (LGPL-2.1-or-later / GPL-2.0-or-later as built by Ubuntu and Debian, whose `libavcodec` also hard-depends on the GPL, patent-encumbered `libx264` encoder library) — deliberately **not included** in any published image. The `hls2mxl` and `file-player` apps need the FFmpeg decoders (H.264/AAC) and install `gstreamer1.0-libav` from the Ubuntu archive **at container start**, same pattern as above. The **mxl-clip-player** demo image does the same from the Debian archive (its entrypoint installs the decoders on first start).

## LGPL corresponding source

The LGPL-licensed binaries in the published images (GStreamer, libnice, and other distribution libraries) are unmodified Ubuntu 24.04 (noble) packages — Debian 13 (trixie) packages for the demo images. Corresponding source for any of them can be obtained from the [Ubuntu package archive](https://packages.ubuntu.com/noble/) or the [Debian package archive](https://packages.debian.org/trixie/), or with `apt-get source <package>` on a matching system, for the exact package versions recorded in each image (`dpkg -l` inside the image). No GPL-licensed codec packages are included in the published images; they are installed on the deployer's machine at container start where required (see above).

## Per-image SBOMs

This file is a component-level overview. The published images additionally carry a full **SPDX SBOM attestation** (and provenance), generated with syft at build time via `docker buildx build --sbom=true` (see `how_to_build.md`, Step 4). Retrieve any image's SBOM with:

```sh
docker buildx imagetools inspect ghcr.io/cbcrc/<image>:latest --format '{{ json .SBOM }}'
```