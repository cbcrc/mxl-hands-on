This repository contains content licensed under two different licenses.

## Code and Configuration Files

All code and configuration files are licensed under the Apache License, Version 2.0. This includes, but is not limited to:
- Shell scripts (`.sh`)
- YAML files (`.yaml`)
- Dockerfiles (`Dockerfile.*.txt`)
- JSON files (`.json`)
- Git configuration files (`.gitignore`, `.gitmodules`)

A full copy of this license is available in the `LICENSES/Apache-2.0.txt` file.

Code and configuration files carry an SPDX header identifying the copyright holder and license:

```
SPDX-FileCopyrightText: <year> CBC/Radio-Canada
SPDX-License-Identifier: Apache-2.0
```

Files that cannot carry comments (e.g. `.json` files, media assets) do not have a header and are covered by the statements in this document. Files copied or derived from other projects (e.g. flow definition JSON files derived from `dmf-mxl` examples) retain their original attribution.

## Documentation and Media

All documentation and media files are licensed under the Creative Commons Attribution 4.0 International License (CC BY 4.0). This includes, but is not limited to:
- Markdown files (`.md`)
- Image files and videos (`.jpg`, `.png`, `.ts`, etc.)

A full copy of this license is available in the `LICENSES/CC-BY-4.0.txt` file.

## Submodule

The `dmf-mxl` git submodule is a separate work licensed under its own Apache License, Version 2.0 (see `dmf-mxl/LICENSE.txt`). Its license is unchanged by this repository.

## Third-Party Components in Container Images

The container images built from this repository bundle third-party open-source components (GStreamer, FFmpeg, CEF, Vosk models, etc.) under their own licenses. See the top-level `THIRD-PARTY-NOTICES.md` file for details.