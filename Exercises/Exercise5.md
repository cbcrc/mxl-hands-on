## Exercise 4 - Full open source DMF with Nmos support

### Synopsis

In this exercise, we will compile the latest commit of the MXL SDK including rust bindings and the rust Gstreamer plugins. Then we will build a full stream augmentation workflow supported by various open source project, including MediaMTX, CEF, **need to complete the full list**.

```mermaid
    flowchart LR
        subgraph HostNode [Host / Compute Node]
            direction LR

            %% --- Stage 1: Sources ---
            subgraph Sources [Sources]
                direction TB
                HLS2MXL[HLS2MXL]
                LoopPlayer[File Player]
                TestGen[Test Generator]
                WebRTC2MXL[WebRTC2MXL]
            end

            %% --- Stage 2: Processing ---
            subgraph Processing [Processing]
                direction TB
                AudioMix[Audio Mix]
                HTML5Keyer[HTML5 Keyer]
                SPXGraphics[SPX Graphics]
            end

            %% --- Stage 3: Output ---
            MXL2SRT[MXL2SRT]

            %% =========================================
            %% CONNECTIONS
            %% =========================================

            %% --- Solid Video (blue) Connections (Indices 0 to 4) ---
            %% Video Sources to Input selector
            HLS2MXL -- IN 1 --> InputSel
            LoopPlayer -- IN 2--> InputSel
            TestGen -- IN 3 --> InputSel
            

            %% Input Selector to Processing
            InputSel --> HTML5Keyer
            
            %% Processing to Output
            HTML5Keyer --> MXL2SRT

            %% --- Solid Audio (green) Connection (Indices 5 to 7) ---
            %% Audio Sources to Audio Mixer
            WebRTC2MXL -- IN 2 --> AudioMix
            HLS2MXL -- IN 1 --> AudioMix

            %% Audio Processing to/From MXL
            AudioMix --> MXL2SRT

        end

        %% --- Legend (Outside the Compute Node) (Indices 8 to 10) ---
    subgraph Legend [Diagram Key]
        direction LR
        k1[Gstreamer</br>based app] -- MXL Video flow --> kt1[Gstreamer</br>based app]
        k2[Gstreamer</br>based app] -- MXL Audio flow --> kt2[Gstreamer</br>based app]
        k4[HTML 5 Graphic</br>engine]
    end

    %% Positioning the legend below the main node
    HostNode ~~~ Legend

        %% ===========================================
        %% STYLING
        %% ===========================================

        %% Node Styling
        classDef gstreamer fill:#66b3ff,color:black,stroke:#333,stroke-width:2px;
        classDef control fill:#007bff,color:#fff,stroke:#333,stroke-width:2px;
        classDef other fill:#cce6ff,color:black,stroke:#333,stroke-width:2px

        class HLS2MXL,LoopPlayer,TestGen,WebRTC2MXL,InputSel,AudioMix,HTML5Keyer,MXL2SRT,k1,kt1,k2,kt2,k3 gstreamer
        class NmosRegistry,DummyNmosNode,NmosController,kt3 control
        class SPXGraphics,k4 other

        %% Link Styling
        %% Solid blue for video connections
        linkStyle 0,1,2,3,4,8 stroke:blue,stroke-width:2px

        %% Solid green for audio connections
        linkStyle 5,6,7,9 stroke:green,stroke-width:2px

```

### Steps

1. Navigate to exercise 5 working directory
    ```sh
        cd ~/mxl-hands-on/docker/exercise-5
    ```
1. Start the system with the start script.
    ```sh
        ./start.sh # For linux based machine
    ```
    ```sh
        ./start-mac.sh # For mac based machine
    ```
1. Use the application and try to reproduce the workflow above. You have more documentation on application usage [here](../gst-apps/README.md)

| App | URL | API Swagger Page |
|-----|-----|-----|
| Test Generator | http://localhost:9600 | http://localhost:9600/docs |
| MXL Info GUI | http://localhost:9699 | http://localhost:9699/docs |
| MXL to WebRTC | http://localhost:9601 | http://localhost:9601/docs |


Reference HLS stream that are 1920x1080p60
    ```sh
        https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_fmp4/master.m3u8
    ```
    ```sh
        https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8
    ```