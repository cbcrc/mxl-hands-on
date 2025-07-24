# Media Exchange Layer (MXL) HANDS ON
Welcome to this guided workshop around the MXL SDK. MXL is an open source SDK to enable seamless real-time in memory exchange of video, audio, and timed metadata between media functions within modern, software-driven, distributed media production environments. 

As the media industry transitions from traditional hardware-based setups to virtualized and containerized production environments, the need for scalable, interoperable software solutions has never been greater. The MXL Project aims to establish an open framework for real-time media exchange, reducing infrastructure complexity and ensuring seamless integration across compute nodes, production clusters, and broadcast platforms. MXL provides an implementation of the Media Exchange Layer defined in the Dynamic Media Facility Reference Architecture as published by the EBU.


### The MXL Project will provide the foundation for:
* **Interoperable software-based media production** – Enabling broadcasters to optimize workflows by seamlessly integrating diverse production tools and compute environments.
* **Accelerating industry-wide adoption of software-defined infrastructure** – Helping media companies adopt software solutions for all tiers of production and for all levels of complexity, including workflows that are latency or quality sensitive.

### [Preparation Windows 11 - Getting WSL (Alma) and Docker ready (Currently not working for exercise 3)](./Preparation/WSL-Alma.md)

### [Preparation Windows 11 - Getting WSL (Ubuntu) and Docker ready](./Preparation/WSL-Ubuntu.md)

### [Preparation Mac - Getting Docker installed and creating a RamDisk (Currently not working on ARM CPU)](./Preparation/MAC.md)

### [Excercise 1 - Single writer and single domain](./Exercises/Exercise1.md)

### [Excercise 2 - Multiple writers and multiple domains](./Exercises/Exercise2.md)

### [Excercise 3 - Add VNC client for GUI and changing the attribute of one of the writer app](./Exercises/Exercise3.md)

## TODO

* Upgrade images to latest MXL version
* Create a git pipeline that will automatically update the images to the latest MXL code

## Authors

* Felix Poulin: initial idea
* Mathieu Rochon: Excercise design
* Anthony Royer: Excercise design
* Sunday Nyamweno: Excercise design and implementation 
