## Guide to test various raster using the Gstreamer plugin.

## Steps

1. Update the submodule to latest commit of main branch (execute from the root of the repository).
    ```sh
        cd dmf-mxl
        git checkout main
        git pull origin main
        cd ..
    ```
1. Tell git to ignore any change to the submodule. This is only needed if you intent to publish back to the remote as we want to keep the remote on the official release hash, not the latest and we also want to ignore all the build artefact.
    ```sh
        git update-index --assume-unchanged dmf-mxl
    ```
1. Build the MXL SDK by running the build script.
    ```sh
        ./build_linux.sh
    ```
1. Build the rust image needed to compile the rust binding and Gstreamer plugins.
    ```sh
        UID=$(id -u) GID=$(id -g) docker compose -f docker-compose.rust-build.yml build
    ```
1. Use the image to build rust binding and Gstreamer plugins.
    ```sh
        UID=$(id -u) GID=$(id -g) docker compose -f docker-compose.rust-build.yml run --rm rust-build
    ```
1. Edit your terminal config file to add paths of Gstreamer plugins and MXL library so it is present on all terminal you open.
    **For .zshrc terminal**
    ```sh
        echo '' >> ~/.zshrc
        echo '# MXL environment' >> ~/.zshrc
        echo 'export GST_PLUGIN_PATH="$HOME/mxl-hands-on/dmf-mxl/rust/target/release"' >> ~/.zshrc
        echo 'export LD_LIBRARY_PATH="$HOME/mxl-hands-on/dmf-mxl/build/Linux-Clang-Release/lib:$HOME/mxl-hands-on/dmf-mxl/build/Linux-Clang-Release/lib/internal"' >> ~/.zshrc
        source ~/.zshrc
    ```
    **For .bashrc terminal**
    ```sh
        echo '' >> ~/.bashrc
        echo '# MXL environment' >> ~/.bashrc
        echo 'export GST_PLUGIN_PATH="$HOME/mxl-hands-on/dmf-mxl/rust/target/release"' >> ~/.bashrc
        echo 'export LD_LIBRARY_PATH="$HOME/mxl-hands-on/dmf-mxl/build/Linux-Clang-Release/lib:$HOME/mxl-hands-on/dmf-mxl/build/Linux-Clang-Release/lib/internal"' >> ~/.bashrc
        source ~/.bashrc
    ```
1. Run the raster test suite.
    ```sh
        UID=$(id -u) GID=$(id -g) docker compose -f docker-compose.gst-test.yml run --rm gst-test 
    ```
1. Run the frame rate test suite.
    ```sh
        UID=$(id -u) GID=$(id -g) docker compose -f docker-compose.framerate-test.yml run --rm framerate-test
    ```