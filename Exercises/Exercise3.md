## Excercise 3 - Add VNC client for GUI and changing the attribute of one of the writer app

### Synopsis

In Exercise 3, we will enhance our MXL environment by integrating a **VNC client and a lightweight Linux desktop container**. This setup will provide a graphical interface, allowing you to **visualize the actual video output** from the MXL writer applications. Building on this, you will then learn how to **modify attributes of an MXL writer application**, specifically changing the overlay text on one of the video flows, and observing these changes live through the VNC viewer. This exercise will provide a tangible demonstration of MXL's video writer and reader test applications.

<img src="./exercise3.png" width="640">

### Steps

1. Go to excercise 3 folder  
   ```sh
   cd /home/lab/nts-hands-on/docker/excercise-3
   ```
1. Look at the docker-compose.yaml file and notice the addition of the VNC-Viewer container. This container is there to give you acces to a desktop in order to be able to see video at the end of the excercise.
On your PC (if you are onsite in MTL), go to VNC web browser <<IP_ADDRESS>>:5900. If you are through VPN or elsewhere in Canada, you can RDP here in order to do so: 10.164.50.197 (credential to be provided)  
   ```sh
   cat docker-compose.yaml
   ```
1. Start the containers
   ```sh
   docker compose up -d
   ```
1. On your PC (if you are onsite in MTL), go to VNC web browser <<IP_ADDRESS>>:5900. If you are through VPN or elsewhere in Canada, you can RDP here in order to do so: 10.164.50.197 (credential to be provided)
1. To install all the Gstreamer dependencies on your linux desktop, go to `Start Menu > System Tools > LXTerminal`  
   ```sh
   cd /root
   chmod +x install.sh
   ./install.sh # This can take afew minutes to upack mxl and install gstreamer
   cd mxl-gst-sink-250509
   ls /domain
   ./mxl-gst-videosink -d /domain -f flowId # use one of the flow ID from the ls /domain command
   ```
1. Close the Gstreamer window and CTRL break the LXTerminal.  
1. Look at the other flow ID in the the domaine.  
   ```sh
   ls /domain
   ```
1. In LXTerminal, `./mxl-gstvideosink -d /domain -f /flowId` using the other flow ID from the `ls /domain` command you previously did. Can you spot the difference and identify where it is comming from?  
Pay attention to the 2 lines in the docker-compose.yaml file that start the writer app in both writer container. One of them looks like this:
   ```sh  
   command: ["/app/mxl-gst-videotestsrc", "-d", "/domain", "-f", "/app/v210_flow.json", "-t", "Original Flow"]
   ```
   Look at the -t parameter. This is an optional paramter of the videotestsrc app. Can you try modifying one of the writer app with new text and observe the change with what you learned so far?

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
### [Back to main page](../README.md)