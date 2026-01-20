# Exercise 4 Data Directory

This directory is used to store video files that can be used as input for the FFmpeg writer in Exercise 4.

## Usage

Place your video files in this directory. Supported formats include:
- MP4
- MKV
- AVI
- MOV
- TS (Transport Stream)
- Any format supported by FFmpeg

## Example

To use a video file in the docker-compose.yaml:

1. Copy your video file to this directory:
   ```sh
   cp /path/to/your/video.mp4 ./data/sample.mp4
   ```

2. Update the `ffmpeg-writer` command in docker-compose.yaml:
   ```yaml
   command: >
     ffmpeg -re -i /data/sample.mp4
     -c:v v210
     -f mxl
     -video_flow_id 11111111-2222-3333-4444-555555555555
     /domain
   ```

## SDP Files

If you configure the ffmpeg-rtp-streamer to generate an SDP file, it will be saved to this directory and can be used by media players:

```sh
ffplay -protocol_whitelist file,udp,rtp -i ./data/stream.sdp
```

## Sample Videos

You can download sample videos for testing:
- [Big Buck Bunny](http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4)
- [Sintel](http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/Sintel.mp4)

Example:
```sh
cd data
wget http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4
```
