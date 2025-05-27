# EBU NTS Media Exchange Layer (MXL) HANDS ON

## Excercise 1 - Introduction

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

Add Web based VNC front end to docker compose [`tiryoh/ubuntu-desktop-lxde-vnc`](https://hub.docker.com/r/tiryoh/ubuntu-desktop-lxde-vnc) To allow users to visualise their modifications on `gstreamer`


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
