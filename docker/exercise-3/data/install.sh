#!/bin/bash
tar -xvf /root/portable-mxl-reader-x86_64.tar.gz -C /root
apt-get update
apt-get install -y gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-x 