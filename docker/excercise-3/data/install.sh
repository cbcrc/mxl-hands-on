#!/bin/bash
tar -xzf /root/mxl.tar.gz -C /root
apt-get update
apt-get install -y gstreamer1.0-plugins-good gstreamer1.0-x 