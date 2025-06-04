# EBU NTS Media Exchange Layer (MXL) HANDS ON

## Excercise 1 - Introduction

### Synopsis


**In excercise 1 we will have 2 docker containers running. One MXL writer writing one video flow and  
one MXL reader reading it. We will explore the following important concepts of MXL:  
 - MXL domain and its structure, which is the base folder where flows are stored.  
 - The NMOS IS-04 Flow resource definition. A .json file that describe a unique flow. Stored in the MXL domain.  
 We will then use the mxl-info tool to list the available flow of the MXL domain and inspect it.**


1. Clone repo
1. Go to exercise 1 folder
1. Look at the docker-compose.yaml file and notice the volume used by both containers
1. Start the containers with the provided .yaml file
1. Look at the containers running
1. Look at the MXL domain file structure inside the container
1. Have a look in all the sub repository of /domain
1. Look at the MXL domain file structure on the host
1. Confirm that the MXL domain file structure is mounted in ram
1. Look at the NMOS IS-04 Flow definition in the /domain/flowId.mxl-flow/video.json and observe the parameters
1. Use mxl-info to get flow information from the mxl reader, you can use watch in front of the command to have live update

### Commands at a glance

```sh
git clone https://snyamweno@bitbucket.org/snyamweno/nts-hands-on.git
cd nts-hands-on/docker/excercise-1
cat docker-compose.yaml
docker compose up -d
docker ps
docker exec -it excercise-1-reader-media-function-1 ls /domain
docker exec -it excercise-1-reader-media-function-1 ls /domain/subFolders
ls /dev/shm/mxl
df -h /dev/shm/mxl
docker exec -it excercise-1-reader-media-function-1 cat /domain/flowId.mxl-flow/video.json
docker exec -it excercise-1-reader-media-function-1 /app/mxl-info -d /domain -f flowId
```

### Extra information

MXL domain files structure explained

|Path|Description|
|:---|:----------|
|${mxlDomain}/|Base directory of the MXL domain|
|${mxlDomain}/${flowId}.mxl-flow/|Directory containing resources associated with a flow with uuid ${flowId}|
|${mxlDomain}/${flowId}.mxl-flow/data|Flow header. contains metadata for a flow ring buffer. Memory mapped by readers and writers.|
|${mxlDomain}/${flowId}.mxl-flow/.json|NMOS IS-04 Flow resource definition.|
|${mxlDomain}/${flowId}.mxl-flow/.access|File 'touched' by readers (if permissions allow it) to notify flow access.  
Enables reliable 'lastReadTime' metadata update.|
|${mxlDomain}/${flowId}.mxl-flow/grains/|Directory where individual grains are stored.|
|${mxlDomain}/${flowId}.mxl-flow/grains/${grainIndex}|Grain Header and optional payload (if payload is in host memory and not device memory ).  
Memory mapped by readers and writers|

The *tmpfs* file system in linux is mounted in ram. This is confirming that the flow frame buffer lives in memory. If you want to read more about  
*tmpfs*, here is some documentation: https://www.kernel.org/doc/html/latest/filesystems/tmpfs.html

In the NMOS IS-04 Flow definition file (.json), it is interesting to note the media_type parameter. This indicate the data model used to store  
the media into memory. The initial development of MXL only support V210 for video but eventually other data model will be supported.

In the ouput of mxl-info, the *grain count* value represent the depth of the buffer for that particular flow. The Latency information is also  
interesting. Since this writer is writing grain that have a size of 1 frame, depending of when you are executing the command, the latency value  
will be between 0 and 33 msec because the frame rate is set to 30000/1001.

## Excercise 2 - Multiple senders

1. Clone repo
1. Go to excercise 2 folder
1. Modify docker-compose file with your text 
	* Explain importance of json file and grains
1. Inspect `/doman` to see how the files are written form both sources
 

### Commands at a glance

```sh
git clone https://snyamweno@bitbucket.org/snyamweno/nts-hands-on.git
cd nts-hands-on/docker/excercise-2
docker compose up -d
docker ps
docker exec -it excercise-2-reader-media-function-1 /app/mxl-info -d /domain -l
docker exec -it excercise-2-reader-media-function-1 ls /domain
```
Expected output

```
 --- MXL Flows ---
	93abcf83-c7e8-41b5-a388-fe0f511abc12
	5fbec3b1-1b0f-417d-9059-8b94a47197ed
```

## Excercise 3 - Add VNC client for GUI

1. Go to VNC web browser <<IP_ADDRESS>>:5900
1. Go to `Start Menu > System Tools > LXTerminal`
	```sh
	cd /root
	chmod +x install.sh
	./install.sh # This can take afew minutes to upack mxl and install gstreamer
	cd mxl-sink
	ls /domain
	./mxl-gst-videosink -d /domain -f 5fbec3b1-1b0f-417d-9059-8b94a47197ed # change flowID from ls command
	```
1. Enjoy memory video memory sharing.

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
