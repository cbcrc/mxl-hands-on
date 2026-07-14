# Media Exchange Layer (MXL) HANDS ON
Welcome to this guided workshop around the MXL SDK. MXL is an open source SDK to enable seamless real-time in memory exchange of video, audio, and timed metadata between media functions within modern, software-driven, distributed media production environments. 

As the media industry transitions from traditional hardware-based setups to virtualized and containerized production environments, the need for scalable, interoperable software solutions has never been greater. The MXL Project aims to establish an open framework for real-time media exchange, reducing infrastructure complexity and ensuring seamless integration across compute nodes, production clusters, and broadcast platforms. MXL provides an implementation of the Media Exchange Layer defined in the Dynamic Media Facility Reference Architecture as published by the EBU.


### The MXL Project will provide the foundation for:
* **Interoperable software-based media production** – Enabling broadcasters to optimize workflows by seamlessly integrating diverse production tools and compute environments.
* **Accelerating industry-wide adoption of software-defined infrastructure** – Helping media companies adopt software solutions for all tiers of production and for all levels of complexity, including workflows that are latency or quality sensitive.

### [Preparation Windows 11 - Getting WSL (Alma) and Docker ready](./Preparation/WSL-Alma.md)

### [Preparation Windows 11 - Getting WSL (Ubuntu) and Docker ready](./Preparation/WSL-Ubuntu.md)

### [Preparation Mac - Getting Docker installed and creating a RamDisk](./Preparation/MAC.md)

### [Exercise 1 - Single writer and single domain](./Exercises/Exercise1.md)

### [Exercise 2 - Multiple writers and multiple domains](./Exercises/Exercise2.md)

### [Exercise 3 - Add VNC client for GUI and changing the attribute of one of the writer app](./Exercises/Exercise3.md)

### [Exercise 4 - Explore audio and video in a real DMF ecosystem with Gstreamer based mxl applications.](./Exercises/Exercise4.md)

## TODO

* Create a git pipeline that will automatically update the images to the latest MXL code

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