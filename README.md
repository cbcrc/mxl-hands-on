# EBU NTS Media Exchange Layer (MXL) HANDS ON

## Excercise 1 - Single writer and single domain

### Synopsis

In Exercise 1, we will set up two Docker containers: one MXL writer to generate a video flow and one MXL reader to consume it. This exercise will introduce you to fundamental MXL concepts:

* MXL Domain: The foundational directory structure where all MXL flows are stored.
* NMOS IS-04 Flow Resource Definition: A JSON file that uniquely describes an MXL flow within the domain.

You will then use the mxl-info tool to list and inspect the available flow within the MXL domain, providing insight into its characteristics.

![Exercise_1](https://bitbucket.org/snyamweno/nts-hands-on/src/main/Images/exercise1.png)

### Steps

1. Clone repo  
```git clone https://snyamweno@bitbucket.org/snyamweno/nts-hands-on.git```
1. Go to exercise 1 folder  
```cd nts-hands-on/docker/excercise-1```
1. Look at the docker-compose.yaml file and notice the volume used by both containers  
```cat docker-compose.yaml```
1. Start the containers with the provided .yaml file  
```docker compose up -d```
1. Look at the containers running  
```docker ps```
1. Look at the MXL domain file as seen by the reader app.  
```docker exec -it excercise-1-reader-media-function-1 ls /domain```
1. Have a look in all the sub repository of /domain  
```docker exec -it excercise-1-reader-media-function-1 ls /domain/subFolders```
1. Look at the MXL domain_1 file structure on the host  
```ls /dev/shm/mxl/domain_1```
1. Confirm that the MXL domain file structure is mounted in ram by confirming the filesystem is *tmpfs*  
```df -h /dev/shm/mxl```
1. Look at the NMOS IS-04 Flow definition in the /domain/flowId.mxl-flow/video.json and observe the parameters  
```docker exec -it excercise-1-reader-media-function-1 cat /domain/flowId.mxl-flow/video.json```
1. Use mxl-info to get flow information from the mxl reader, you can use watch in front of the command to have live update  
```docker exec -it excercise-1-reader-media-function-1 /app/mxl-info -d /domain -f flowId```
1. Look inside the repository of the grains on the host and confirm that you have all the grain according to the grain count value observed in the step before  
```ls /dev/shm/mxl/domain_1/flowId.mxl-flow/grains```
1. Shut down the containers of excercise 1  
```docker compose down```
1. look at the MXL domain file structure on the host again and notice that the file are gone.  
```ls /dev/shm/mxl/domain_1```


### Extra information

The MXL domain is a fundamental concept in the Media Exchange Layer. It acts as the central shared memory space where all media flows and their associated metadata reside. Understanding its file structure is important for working with MXL.

#### MXL Domain File Structure Explained
The MXL domain follows a specific hierarchy to organize flows and their data. Here's a breakdown of the key components within `${mxlDomain}` (the base directory of your MXL domain, e.g., `/dev/shm/mxl/domain_1` in this exercise):

|Path|Description|
|:---|:---|
|${mxlDomain}/|Base directory of the MXL domain|
|${mxlDomain}/${flowId}.mxl-flow/|Directory containing resources associated with a flow with uuid ${flowId}|
|${mxlDomain}/${flowId}.mxl-flow/data|Flow header. contains metadata for a flow ring buffer. Memory mapped by readers and writers.|
|${mxlDomain}/${flowId}.mxl-flow/.json|NMOS IS-04 Flow resource definition.|
|${mxlDomain}/${flowId}.mxl-flow/.access|File 'touched' by readers (if permissions allow it) to notify flow access. Enables reliable 'lastReadTime' metadata update.|
|${mxlDomain}/${flowId}.mxl-flow/grains/|Directory where individual grains are stored.|
|${mxlDomain}/${flowId}.mxl-flow/grains/${grainIndex}|Grain Header and optional payload (if payload is in host memory and not device memory ). Memory mapped by readers and writers|

#### Understanding `tmpfs` and Memory-Mapped I/O
In Step 9, you confirmed that the MXL domain is mounted on a `tmpfs` filesystem. This is a very important design choice for MXL.

As noted, `tmpfs` is a temporary file storage facility in Unix-like operating systems that resides entirely in volatile memory (RAM), not on a persistent disk.  
  
You can read more about `tmpfs` here: https://www.kernel.org/doc/html/latest/filesystems/tmpfs.html


#### NMOS IS04 flow definiion  
`${mxlDomain}/<flowId>.mxl-flow/.json`: This JSON file is the NMOS IS-04 Flow Resource Definition. It is crucial as it uniquely describes the characteristics of the MXL flow. Key parameters you observed include:  

* `id`: The unique identifier (flowId) for this specific flow.
* `label`: A human-readable label for the flow.
* `media_type`: Crucially, this indicates the data model used to store the media into memory. For the initial development of MXL, this will be `"media_type": "video/v210"`, signifying uncompressed 10-bit YCbCr 4:2:2 video. As MXL evolves, other media_type values will be supported for different data formats.
* Other parameters like `frame_width`, `frame_height`, `interlace_mode`, `colorspace`, `components`, etc., provide detailed technical specifications of the video flow.

#### Interpreting mxl-info Output
Step 11 introduces you to the `mxl-info` tool, which is invaluable for inspecting the live state of an MXL flow. When you `run mxl-info -d /domain -f flowId`, pay close attention to the following fields:

* `Flow[FlowId]`: Confirms the ID of the flow being inspected.
* `grain count`: This value represents the depth of the circular buffer for that particular flow. It indicates how many historical grains (frames in this exercise) are currently available in the MXL domain for that flow. The mxl-writer continuously overwrites older grains once the buffer depth is reached.
* `latency`: It represents the time difference between the capture/generation timestamp of the latest available grain and the current time when mxl-info is executed.
In this exercise, with the writer generating grains @ 29.97 frames per second (30000/1001), each grain represents 33.36 milliseconds of video.
Therefore, the latency value will fluctuate between 0 and approximately 33 msec. A value close to 0 msec indicates you are very close to when the current grain was completed by the MXL writer, while a value closer to 33 msec means the current grain has been available for almost a full frame interval. This could potentially inticate issue in the system if this value goes beyond the time value of a grain.
* `grain rate`: Displays the nominal framerate of the flow, derived from the NMOS IS-04 definition.

## Excercise 2 - Multiple writers and multiple domains

### Synopsis
Building on the foundational concepts from Exercise 1, this exercise will demonstrate how MXL handles **multiple concurrent video flows** and the concept of **domain separation**.

You will deploy three Docker containers: two MXL writers, each generating a unique video flow, and one MXL reader. We will explore how the reader interacts with multiple flows on the **same MXL domain**, observe the resulting file structure, and then **modify a writer's domain** to understand how flows can be isolated. This hands-on experience will solidify your understanding of MXL's domain-based organization.

### Setps

1. Go to excercise 2 folder  
```cd home/lab/nts-hands-on/docker/excercise-2```
1. Look at the docker-compose.yaml file and notice that we now have 2 writers and that all containers are mapped to the same MXL domain.  
```cat docker-compose.yaml```
1. Start the containers with the provided .yaml file  
```docker compose up -d```
1. Look at the containers running  
```docker ps```
1. Look at the MXL domain file structure as seen by the reader app. Notice the second flow with a new unique ID  
```docker exec -it excercise-2-reader-media-function-1 ls /domain```
1. Look at the MXL domain_1 file structure on the host.  
```ls /dev/shm/mxl/domain_1```
1. Use mxl-info to get flow information from the mxl reader to get information of each flow  
```docker exec -it excercise-2-reader-media-function-1 /app/mxl-info -d /domain -l```
1. Shut down the containers of excercise 2  
```docker compose down```
1. Modify the docker-compose.yaml file to map the second writer to /dev/shm/mxl/domain_2  
```nano docker-compose.yaml and change line 14 for this: source: /dev/shm/mxl/domain_2```
1. Start up the containers with the updated .yaml file  
```docker compose up -d```
1. Look at the MXL domain file structure as seen by the reader app. Notice that we only see the flow of the first writer app after we change the domain of writer 2.  
```docker exec -it excercise-2-reader-media-function-1 ls /domain```
1. Look at the MXL domain_1 and domain_2 file structure on the host and notice that both flows still exist but they are isolated by their MXL domain.  
```ls /dev/shm/mxl/domain_1 and ls /dev/shm/mxl/domain_2```
1. Shudown containers of excercise 2  
```docker compose down```


### Extra information exercise 2
This exercise expands on the foundational concepts introduced in Exercise 1 by demonstrating how MXL handles multiple media flows. Understanding how these flows coexist and how domains can provide isolation.

#### Coexistence of Multiple Flows within a Single Domain
In the initial setup of Exercise 2 (Steps 2 through 7), you observed two MXL writers contributing distinct video flows to the same MXL domain (`/dev/shm/mxl/domain_1`).  

* Unique Flow Identification: Even though both flows share the same root domain, MXL maintains strict separation and identification of each flow. This is achieved through:
	* Unique `flowIds`: As you observed in Step 5 (l`s /domain`), each flow gets its own distinct `flowId` (a UUID), which serves as its unique identifier within the domain.
	* Dedicated Flow Directories: Each `flowId` corresponds to its own dedicated directory (`<flowId>.mxl-flow`) within the domain's file structure. This ensures that the flow definition (e.g., `.json`) and the actual media grains for one flow are completely separate from another.
* `mxl-info -l` for Domain-Wide Overview: Step 7 introduces the `mxl-info -l` command. The `-l` (list) flag is usefull; it instructs mxl-info to scan the specified MXL domain and list all active flows within it. 

#### The Power of MXL Domains for Isolation
The core learning objective of the latter part of Exercise 2 (Steps 8 through 12) is to understand the concept of domain separation in MXL.

* **Logical and Physical Isolation:** By modifying the `docker-compose.yaml` file to map writer-2 to `/dev/shm/mxl/domain_2`, you effectively writing to a second, entirely separate MXL domain.
	* **Logical Isolation:** From the perspective of applications, a flow existing in `domain_1` is completely distinct and inaccessible to an application configured only to read from `domain_2`, and vice-versa.
	* **Physical Isolation:** As you confirmed in Step 12 (`ls /dev/shm/mxl/domain_1` and `ls /dev/shm/mxl/domain_2`), the two domains exist as independent directory structures on the host's `tmpfs` filesystem.
* **Use Cases for Multiple Domains:** The ability to establish multiple, isolated MXL domains is a fundamental feature for various architectural patterns:
	* **Security:** Different applications or user groups can be granted access only to specific domains, ensuring that sensitive media flows are isolated from less secure ones.
	* **Workload Separation:** High-sensitivity workflows, like playout, can be isolated in their own domain to prevent interference from other, less critical workflows, ensuring consistent operation of critical systems.
	* **Organizational Boundaries:** In large media systems, different production groups might operate their own distinct MXL domains to manage their media assets independently.
	* **Resource Management:** While tmpfs uses shared memory, creating separate domains can simplify resource allocation and monitoring for distinct sets of flows.
	* **Scalability:** While not directly demonstrated in this exercise, the concept of domains facilitates distributed architectures where different parts of a system might manage their own local MXL domains.

**Key Takeaway:** The concept of domains in MXL allows you to set up clear boundaries for your media workflows. Whether for security, isolation, organizational purposes, or robust resource management, multiple MXL domains provide the necessary separation and control for complex media exchange environments.

## Excercise 3 - Add VNC client for GUI and changing the attribute of one of the writer app

### Synopsis

In Exercise 3, we will enhance our MXL environment by integrating a **VNC client and a lightweight Linux desktop container**. This setup will provide a graphical interface, allowing you to **visualize the actual video output** from the MXL writer applications. Building on this, you will then learn how to **modify attributes of an MXL writer application**, specifically changing the overlay text on one of the video flows, and observing these changes live through the VNC viewer. This exercise will provide a tangible demonstration of MXL's video writer and reader test applications.

### Steps

1. Go to excercise 3 folder  
```cd home/lab/nts-hands-on/docker/excercise-3```
1. Look at the docker-compose.yaml file and notice the addition of the VNC-Viewer container. This container is there to give you acces to a desktop in order to be able to see video at the end of the excercise.
On your PC (if you are onsite in MTL), go to VNC web browser <<IP_ADDRESS>>:5900. If you are through VPN or elsewhere in Canada, you can RDP here in order to do so: 10.164.50.197 (credential to be provided)  
```cat docker-compose.yaml```
1. To install all the Gstreamer dependencies on your linux desktop, go to `Start Menu > System Tools > LXTerminal`  
   ```sh
   cd /root
   chmod +x install.sh
   ./install.sh # This can take afew minutes to upack mxl and install gstreamer
   cd mxl-sink
   ls /domain
   ./mxl-gst-videosink -d /domain -f flowId # use one of the flow ID from the ls /domain command
   ```
1. Close the Gstreamer window and CTRL break the LXTerminal.  
1. Look at the other flow ID in the the domaine.  
```ls /domain```
1. In LXTerminal, `./mxl-gstvideosink -d /domain -f /flowId` using the other flow ID from the `ls /domain` command you previously did. Can you spot the difference and identify where it is comming from?  
```Pay attention to the 2 lines in the docker-compose.yaml file that start the writer app in both writer container. One of them looks like this: ```  
   ```sh  
   command: ["/app/mxl-gst-videotestsrc", "-d", "/domain", "-f", "/app/v210_flow.json", "-t", "Original Flow"]
   ```
   ```Look at the -t parameter. This is an optional paramter of the videotestsrc app. Can you try modifying one of the writer app with new text and observe the change with what you learned so far? ```

### Extra information for Exercise 3
Exercise 3 is transitioning from theoretical understanding and command-line inspection to direct visual confirmation of MXL's functionality. It also introduces the concept of dynamically influencing media flows, a key aspect of real-time broadcast and production environments.

#### Understanding the VNC Client and Graphical Interface
The addition of the `VNC-Viewer` Docker container serves several critical purposes:

* **Visualizing MXL Flows:** While `mxl-info` provides valuable metadata, seeing the actual video stream confirms that the MXL writer is correctly generating video grains and that the `mxl-gst-videosink` (a GStreamer-based MXL reader application) is successfully consuming and rendering them. This provides immediate, tangible feedback on the entire MXL skd.
* **Emulating a Media Function:** The VNC container, with its GStreamer installation, effectively acts as a "media function" – a common component in broadcast systems that processes or displays media. It demonstrates how a separate application can interface with the MXL domain to access and utilize media streams.


#### Exploring `mxl-gst-videotestsrc` and `mxl-gst-videosink`  
* `mxl-gst-videotestsrc` **(Writer):** This is the GStreamer-based application used by your MXL writers to generate video. In this exercise, you're interacting with the `-t` parameter.
	* The `-t` (text overlay) parameter allows you to add a customizable text string directly onto the video frames generated by the `videotestsrc`.
* `mxl-gst-videosink` **(Reader/Viewer):** This application acts as an MXL reader and uses GStreamer to display the video.
	* It takes the `-d` (domain) and `-f` (flow ID) parameters to specify which MXL domain to connect to and which specific flow to consume.
	* It translates the MXL grains (V210 format in this case) into a displayable video stream.

## TODO

* Upgrade image to latest MXL version
* Publish on `company` Docker hub instead of `deplops`
* Move repo to corprate project
* Write docker -compse for Excercise-3

## Authors

* Felix Poulin: initial idea
* Mathieu Rochon: Excercise design
* Anthony Royer: Excercise design
* Sunday Nyamweno: Excercise design and implementation 
