## Preparation - Installing WSL and Docker

### Synopsis

In order to run these exercises on a Mac, you need to install Docker and create a RAM disk. Theses preparation steps will help you to install Docker and setup a RAM disk on your Mac.

### Installing Docker on MAC

### Steps

1. Download the Docker.dmg file from the following link
   ```sh
   https://desktop.docker.com/mac/main/arm64/Docker.dmg?utm_source=docker&utm_medium=webreferral&utm_campaign=docs-driven-download-mac-arm64&_gl=1*nqtzhd*_ga*MTA4ODM0MjI1NC4xNzUxOTA4NjEy*_ga_XJWPQMJYHQ*czE3NTMyMDkxODckbzUkZzEkdDE3NTMyMDkyNjAkajYwJGwwJGgw
   ```
1. Open a terminal and go to the Downloads folder
   ```sh
   cd ./Downloads
   ```
1. Find your username
   ```sh
   ls /Users
   ```
1. Install Docker from CLI
   ```sh
   sudo hdiutil attach Docker.dmg
   ```
   ```sh
   sudo /Volumes/Docker/Docker.app/Contents/MacOS/install --accept-license --user=<username>
   ```
   ```sh
   sudo hdiutil detach /Volumes/Docker
   ```
1. Go back to your user folder
   ```sh
   cd ..
   ```
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