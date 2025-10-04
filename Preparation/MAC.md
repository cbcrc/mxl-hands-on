## Preparation - Installing Docker, Xcode Command Line Tools and creating a RamDisk

### Synopsis

In order to run these exercises on a Mac, you need to install Docker and create a RAM disk. Theses preparation steps will help you to install Docker and setup a RAM disk on your Mac.

### Installing Docker on MAC !!!DISCLAIMER, DOCKER DESKTOP IS A LICENSED PRODUCT, MAKE SURE YOU ARE FOLLOWING THE LICENSE!!!

### Steps

1. On MacOS with apple silicon it is recommended to install rosetta 2 to work with docker.
   ```sh
   softwareupdate --install-rosetta
   ```
1. Download the Docker.dmg file from the following link https://docs.docker.com/desktop/setup/install/mac-install/. Make sure to select the download link for the right type silicon your Mac is having.
   
1. Double click on the Docker.dmg file to mount it.
1. From the docker volume that just mounted on your desktop. Drag and drop the Docker.app in your Applications folder.
1. Open a terminal window.
1. Start Docker Desktop
   ```sh
   open -a Docker
   ```
1. Verify the Docker installation
   ```sh
   docker run hello-world
   ```

### Installing Xcode Command Line Tools. This will come with git commands.

1. Installing Xcode Command Line Tools. After the CLI command accept the pop-up to install Xcode.
   ```sh
   xcode-select --install
   ```

### Installing Brew

1. Installing Brew, an optional package manager.
   ```sh
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

### Installing FFMPEG

1. Installing FFMPEG with brew.
   ```sh
   brew install ffmpeg
   ```

1. Check ffmpeg installation by generatig a clip.
   ```sh
   ffmpeg -f lavfi -i testsrc -t 10 -pix_fmt yuv420p output.mp4
   ```

1. Check the output.mp4 with your favorite clip player.

### Creating a 512MB RamDisk on your MAC

### Steps

1. Create a 512MB ram disk
   ```sh
   diskutil erasevolume HFS+ mxl $(hdiutil attach -nomount ram://1048576)
   ```
1. Verify that the disk was created
   ```sh
   diskutil list
   ```
1. You are ready to go!!!

### [Back to main page](../README.md)