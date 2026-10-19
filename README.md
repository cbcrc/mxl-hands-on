# Media Exchange Layer (MXL) HANDS ON
Welcome to this guided workshop around the MXL SDK. MXL is an open source SDK to enable seamless real-time in memory exchange of video, audio, and timed metadata between media functions within modern, software-driven, distributed media production environments. 

As the media industry transitions from traditional hardware-based setups to virtualized and containerized production environments, the need for scalable, interoperable software solutions has never been greater. The MXL Project aims to establish an open framework for real-time media exchange, reducing infrastructure complexity and ensuring seamless integration across compute nodes, production clusters, and broadcast platforms. MXL provides an implementation of the Media Exchange Layer defined in the Dynamic Media Facility Reference Architecture as published by the EBU.


### The MXL Project will provide the foundation for:
* **Interoperable software-based media production** – Enabling broadcasters to optimize workflows by seamlessly integrating diverse production tools and compute environments.
* **Accelerating industry-wide adoption of software-defined infrastructure** – Helping media companies adopt software solutions for all tiers of production and for all levels of complexity, including workflows that are latency or quality sensitive.

### [Preparation Windows 11 - Getting WSL (Ubuntu) and Docker ready](./Preparation/WSL-Ubuntu.md)

### [Preparation Mac - Getting Docker installed and creating a RamDisk](./Preparation/MAC.md)

### [Exercise 1 - Single writer and single domain](./Exercises/Exercise1.md)

### [Exercise 2 - Multiple writers and multiple domains](./Exercises/Exercise2.md)

### [Exercise 3 - Watch MXL flows in your browser with a WebRTC player](./Exercises/Exercise3.md)

### [Exercise 4 - Explore audio and video in a real DMF ecosystem with Gstreamer based mxl applications.](./Exercises/Exercise4.md)

## Before the session: download the images

The exercises use pre-built images from `ghcr.io/cbcrc`, all tagged `:latest`. Docker Compose only downloads an image when it is missing, so an image pulled for an earlier session is never updated on its own. Download (or refresh) all images ahead of time, so the session doesn't depend on the venue network:

```sh
cd ~/mxl-hands-on/docker
for ex in exercise-*; do (cd "$ex" && docker compose pull); done
```

## More documentation

* [GStreamer MXL apps](./gst-apps/README.md) - the applications used in Exercise 4
* [Test tools](./test-tools/README.md) - bench instruments for the MXL SDK, including the ABI tester
* [How to build the images](./how_to_build.md) - building the MXL SDK and publishing the images

## Authors

* Felix Poulin: initial idea
* Mathieu Rochon: Exercise design
* Anthony Royer: Exercise design
* Sunday Nyamweno: Exercise design and implementation 

## ⚖️ License

This repository is dual-licensed.

* All **code, configuration, and script files** (e.g., `.sh`, `.yaml`, Dockerfiles) are licensed under the [Apache License 2.0](LICENSES/Apache-2.0.txt).
* All **documentation and media files** (e.g., `.md`, `.jpg`, `.ts`) are licensed under the [Creative Commons Attribution 4.0 International License](LICENSES/CC-BY-4.0.txt).

Please see [`LICENSES/LICENSE.md`](LICENSES/LICENSE.md) for more details. A copy of the Apache License 2.0 is also provided in the top-level [`LICENSE`](LICENSE) file.

The [`dmf-mxl`](https://github.com/dmf-mxl/mxl) git submodule is a separate work licensed under its own Apache License 2.0 (see `dmf-mxl/LICENSE.txt`); its license is unchanged by this repository.

The container images built from this repository bundle third-party open-source components under their own licenses. See [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) for details.