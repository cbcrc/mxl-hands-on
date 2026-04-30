## Exercise 4 - Full open source DMF with Nmos support

### Synopsis

In this exercise, we will compile the latest commit of the MXL SDK including rust bindings and the rust Gstreamer plugins. Then we will build a full stream augemtation workflow supported by various open source Nmos repository.

```mermaid
    flowchart LR
        subgraph HostNode [Host / Compute Node]
            direction LR

            %% --- Stage 1: Sources ---
            subgraph Sources [Sources]
                direction TB
                SRT2MXL[SRT2MXL]
                LoopPlayer[Loop Player]
                LatencyTest[Latency Test]
                WebRTC2MXL[WebRTC2MXL]
            end

            %% --- MXL Domain Stage 1 ---
            MXLDomain1([MXL Domain])

            %% --- Stage 2: Input Selection ---
            InputSel[Input Sel]
            
            %% --- MXL Domain Stage 2 ---
            MXLDomain2([MXL Domain])

            %% --- Stage 3: Processing ---
            subgraph Processing [Processing]
                direction TB
                AudioMix[Audio Mix]
                HTML5Keyer[HTML5 Keyer]
                SPXGraphics[SPX Graphics]
            end

            %% --- SPX Graphic engine ---
            

            %% --- MXL Domain Stage 3 ---
            MXLDomain3([MXL Domain])

            %% --- Stage 4: Output ---
            MXL2SRT[MXL2SRT]

            %% --- Control Plane ---
            subgraph Control [NMOS Control Plane]
                NmosController[NMOS Controller]
                NmosRegistry[NMOS Registry]
                DummyNmosNode[Dummy NMOS Node]
            end

            %% =========================================
            %% CONNECTIONS
            %% =========================================

            %% --- Solid Video (blue) Connections (Indices 0 to 9) ---
            %% Video Sources to MXL
            SRT2MXL --> MXLDomain1
            LoopPlayer --> MXLDomain1
            LatencyTest --> MXLDomain1
            

            %% MXL from/to Input Selector
            MXLDomain1 -- IN 1 ---> InputSel
            MXLDomain1 -- IN 2 ---> InputSel
            MXLDomain1 -- IN 3 ---> InputSel
            InputSel --> MXLDomain2

            %% MXL from/to Processing
            MXLDomain2 --> HTML5Keyer
            HTML5Keyer --> MXLDomain3            

            %% MXL to Output
            MXLDomain3 --> MXL2SRT

            %% --- Solid Audio (green) Connection (Indices 10 to 15) ---
            %% Audio Sources to MXL
            WebRTC2MXL --> MXLDomain2
            SRT2MXL --> MXLDomain2

            %% Audio Processing to/From MXL
            MXLDomain2 -- IN 1 ---> AudioMix
            MXLDomain2 -- IN 2 ---> AudioMix
            AudioMix --> MXLDomain3

            %% --- Dotted Yellow control Connections (Indices 16 to 24) ---
            SRT2MXL <-.-> NmosRegistry
            LoopPlayer <-.-> NmosRegistry
            LatencyTest <-.-> NmosRegistry
            WebRTC2MXL <-.-> NmosRegistry
            InputSel <-.-> NmosRegistry
            HTML5Keyer <-.-> NmosRegistry
            AudioMix <-.-> NmosRegistry
            MXL2SRT <-.-> NmosRegistry
            DummyNmosNode <-.-> NmosRegistry
            NmosController <-.-> NmosRegistry

        end

        %% --- Legend (Outside the Compute Node) ---
    subgraph Legend [Diagram Key]
        direction LR
        k1([MXL Domain]) -- Video Connection --- kt1[Gstreamer</br>based app]
        k2([MXL Domain]) -- Audio Connection --- kt2[Gstreamer</br>based app]
        k3[Gstreamer</br>based app] -- Nmos Connection --- kt3[NMOS ecosystem]
        k4[HTML 5 Graphic</br>engine]
    end

    %% Positioning the legend below the main node
    HostNode ~~~ Legend

        %% ===========================================
        %% STYLING
        %% ===========================================

        %% Node Styling
        classDef mxl fill:#ffe0b3,color:black,stroke:#333,stroke-width:2px;
        classDef gstreamer fill:#66b3ff,color:black,stroke:#333,stroke-width:2px;
        classDef control fill:#007bff,color:#fff,stroke:#333,stroke-width:2px;
        classDef other fill:#cce6ff,color:black,stroke:#333,stroke-width:2px

        class MXLDomain1,MXLDomain2,MXLDomain3,k1,k2 mxl
        class SRT2MXL,LoopPlayer,LatencyTest,WebRTC2MXL,InputSel,AudioMix,HTML5Keyer,MXL2SRT,kt1,kt2,k3 gstreamer
        class NmosRegistry,DummyNmosNode,NmosController,kt3 control
        class SPXGraphics,k4 other

        %% Link Styling
        %% Solid blue for video connections
        linkStyle 0,1,2,3,4,5,6,7,8,9,25 stroke:blue,stroke-width:2px

        %% Solid green for audio connections
        linkStyle 10,11,12,13,14,26 stroke:green,stroke-width:2px

        %% Dotted yellow for nmos connections
        linkStyle 15,16,17,18,19,20,21,22,23,27 stroke:yellow
```

### Steps

1. Update the submodule to latest commit of main branch (execute from the root of the repository).
    ```sh
        cd ~/mxl-hands-on/dmf-mxl
        git checkout main
        git pull origin main
        cd ..
    ```
1. Tell git to ignore any change to the submodule. This is only needed if you intent to publish back to the remote as we want to keep the remote on the official release hash, not the latest and we also want to ignore all the build artefact.
    ```sh
        cd ~/mxl-hands-on
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
1. Navigate to the exercise 5 folder.
    ```sh
        cd ~/mxl-hands-on/docker/exercise-5