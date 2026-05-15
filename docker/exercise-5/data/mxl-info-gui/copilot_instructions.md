# GitHub Copilot Agent Instructions: MXL-INFO web GUI

## Project Overview
Your task is to build a Dockerized MXL probing application that will use the mxl-info CLI command to display status information on MXL flows present in multiple MXL domains. The application uses a custom build CLI command named mxl-info, FastAPI for the backend API, React + Vite for the web frontend. There are many other application already created that use a python back end and a react + vite front end, you can find them here `./docker/exercise-5/data`. You need to keep code consistent between application.

## System Architecture
The application consists of three main components running within a Docker environment:
1. **Frontend:** A web UI built with React and Vite.
2. **Backend / CLI Engine:** A Python application using FastAPI to serve the frontend and expose an API, python subprocess to run the custom build CLI command and a custom made python parser to parse the output of the CLI command to a json to pass it to the front end.

## Environment & File Specifications
- **MXL-info:** The custom CLI command can be found here `./dmf-mxl/build/Linux-Clang-Release/tools/mxl-info` and the instructions on how to use it are found here `./dmf-mxl/docs/tools.md`.

## Backend functionalities:
The python backend must have the following features:
1. A function `get_domains` that will scan for mxl-domain. It will scan for the files named `domain_def.json` in `/mxl-domain`. The content of the file is like this `{"id":"51ef9b5c-98c1-4f98-9def-1d61ee9a4fdb"}`. For each file found, it will store the absolute file path of where the file was found. We will use this information in later features. This function need to run once at startup and will be manually triggered afterward.
2. A function `scan_domain`. It will call `./mxl-info -d AbsoluteFilePath` and store the resulting output in a json to be exposed later by the front end. Here is an example of the output: 
   ```sh
      ./mxl-info -d /Volumes/mxl/domain_1
      28cc59be-3546-515f-8326-fc5639e8a7f0, "HTML5 Keyer – Output", "Media Function 1:Video"
      6dd5a229-f9fc-5445-b748-18df3ff391cc, "Test Generator – Video", "Media Function 1:Video"
      b1fc38a5-7cb3-5b23-ba6c-c3b73d3920da, "Test Generator – Audio", "Media Function 1:Audio"
      0a6fbc49-f73c-5f75-a588-9c10ea83f98b, "Input Selector – Output", "Media Function 1:Video"
   ```
   The first item (separated by comma) is the FlowUUID of the mxl flow, the second is the flow label and the third is the flow grouphint.
3. A function `get_flow_info`, it will call `./mxl-info -d AbsoluteFilePath -f FlowUUID` and store the resulting output in a json to be exposed later by the front end. Here is an example of the output:
   ```sh
      ./mxl-info -d /Volumes/mxl/domain_1 -f 6dd5a229-f9fc-5445-b748-18df3ff391cc
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
	   Latency (grains): 0
	   Active: true
   ```

## Required API & UI Functionalities
The FastAPI backend and the React UI must expose and support the following backend functions:
1. **Scan Domain:** A button that will lunch the backend python function `get_domain` that scan for mxl domain. It need to run once at startup and automatically every 30 seconds (HTTP Polling).
2. **Domain List:** A windows that display all domain found by the `get_domain` function. Displaying both the UUID of the domain and the absolute file path of the domain. This windows need to scale automatically depending on the number of domain found.
3. **Domain Selector**: A dropdown to select the domain you want to explore out of all domain found.
4. **MXL Flow List**: A windows that display all mxl flow found by the `scan_domain` function for the selected domain. Displaying all 3 information of each flow, FlowUUID, Flow Label and Flow Grouphint. This windows need to scale automatically depending on the number of flow found up to 20 flows, after that a scroll bar will be showing to access flow above 20. After a domain as been selected, it will need to run the `scan_domain` function every 30 seconds (HTTP Polling).
5. **Refresh Flow List**: A button that will run the `scan_domain` function and update the list of flow int he MXL Flow List window.
5. **Flow 1 Selector**: A dropdown to select the flow you want to see information about from all the flows found by the `scan_domain` function.
6. **Flow 1 Info Display:** A window that will display the result the `get_flow_info` function. Once a flow is selected in the Flow 1 Selector dropdown, it will run the `get_flow_info` every 0,5 sec and update the display (HTTP Polling).
5. **Flow 2 Selector**: A dropdown to select the flow you want to see information about from all the flows found by the `scan_domain` function.
6. **Flow 2 Info Display:** A window that will display the result the `get_flow_info` function. Once a flow is selected in the Flow 2 Selector dropdown, it will run the `get_flow_info` every 0,5 sec and update the display (HTTP Polling).

## Step-by-Step Implementation Guide for Copilot

**Step 1: Docker Setup**
- Use the `./docker/exercise-5/docker-compose-dev.yml` as a baseline. The Nmos registry node and gstreamer application are already setup as well as the domain volume for MXL usage.
- Ensure the base image installs Python 3, Node.js (for building the React app).

**Step 2: FastAPI & Python Backend**
- Create a FastAPI application on port 9660.
- Implement endpoints: `get_domain`, `scan_domain` and `get_flow_info`.

**Step 3: React + Vite Frontend**
- Initialize a React + Vite project.
- Build a clean, user-friendly control panel with a Scan Domain button, a windows to display the Domain List, a dropdown menu to select the domain, a window that display the MXL flow list of the selected domain, a refresh flow list button, a Flow 1 Selector dropdown menu to select the flow you want to see in the Flow 1 Info display, a Flow 1 info display, a Flow 2 Selector dropdown menu to select the flow you want to see in the Flow 2 Info display, a Flow 2 info display.
- Wire the UI to communicate with the FastAPI backend.
- Serve the built React static files via FastAPI, or run them on a separate port depending on the Docker setup.
- the front end should be exposed on port 9760

Please write the necessary Dockerfiles, Python backend scripts, React components, and integration code following these guidelines at the following location `./docker/exercise-5/data/mxl-info-gui`.