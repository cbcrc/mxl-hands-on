# Exercise 5 – GStreamer Pipelines Reference

Each section below describes one of the six GStreamer-based services, gives the equivalent
`gst-launch-1.0` command, explains what the pipeline does, and includes a Mermaid diagram.

---

## 1. File Player (`gst_player.py`)

### `gst-launch-1.0` command

```bash
gst-launch-1.0 \
  filesrc location="/path/to/media/file" \
  ! decodebin name=dec \
  dec. ! videoconvert ! videoscale \
       ! "video/x-raw,format=v210" \
       ! queue \
       ! mxlsink flow-id="<video-flow-id>" domain="/mxl-domain" \
  dec. ! audioresample ! audioconvert \
       ! "audio/x-raw,format=F32LE,layout=interleaved" \
       ! queue \
       ! mxlsink flow-id="<audio-flow-id>" domain="/mxl-domain"
```

### Explanation

Reads a media file from disk and demuxes/decodes it with `decodebin`.
The decoded video stream is colour-converted and scaled, then a `capsfilter` locks the pixel
format to `v210` — the only format `mxlsink` accepts for video — before the frame is written to
the MXL flow. The decoded audio stream is resampled and converted, then a `capsfilter` locks the
format to `F32LE` interleaved — the only audio format `mxlsink` accepts — before being written to
the second MXL flow. Without these explicit capsfilters the pipeline relies on auto-negotiation
which may land on an incompatible format and cause a hard failure at runtime. Once the file reaches end-of-stream the pipeline seeks back to position 0 to
loop indefinitely.

The pipeline is first brought to `PAUSED` so that `mxlsink` has time to write `flow_def.json`,
then the label/description in that file is patched before playback starts.

### Diagram

```mermaid
flowchart LR
    filesrc["filesrc\nlocation=file"]
    decodebin["decodebin\nname=dec"]
    vconv["videoconvert"]
    vscale["videoscale"]
    vqueue["queue"]
    vsink["mxlsink\nflow-id=video"]
    aresample["audioresample"]
    aconv["audioconvert"]
    aqueue["queue"]
    asink["mxlsink\nflow-id=audio"]

    filesrc --> decodebin
    decodebin -->|"video pad"| vconv --> vscale --> vcaps["capsfilter\nformat=v210"] --> vqueue --> vsink
    decodebin -->|"audio pad"| aresample --> aconv --> acaps["capsfilter\nF32LE, interleaved"] --> aqueue --> asink
```

---

## 2. Test Generator (`gst_generator.py`)

### `gst-launch-1.0` command

```bash
gst-launch-1.0 \
  videotestsrc pattern=19 is-live=true \
    ! "video/x-raw,width=1920,height=1080,framerate=30/1" \
    ! timeoverlay font-desc="Sans Bold 28" halignment=right valignment=bottom shaded-background=true \
    ! textoverlay text="Test Generator" font-desc="Sans Bold 36" halignment=center valignment=top shaded-background=true \
    ! videoconvert \
    ! "video/x-raw,format=v210" \
    ! queue \
    ! mxlsink flow-id="<video-uuid>" domain="/mxl-domain/<domain-uuid>.mxl-domain" \
  audiotestsrc wave=0 freq=1000 volume=0.1 is-live=true \
    ! audioconvert \
    ! "audio/x-raw,format=F32LE,channels=2,rate=48000,layout=interleaved" \
    ! queue \
    ! mxlsink flow-id="<audio1-uuid>" domain="/mxl-domain/<domain-uuid>.mxl-domain" \
  audiotestsrc wave=0 freq=1000 volume=0.1 is-live=true \
    ! audioconvert \
    ! "audio/x-raw,format=F32LE,channels=2,rate=48000,layout=interleaved" \
    ! queue \
    ! mxlsink flow-id="<audio2-uuid>" domain="/mxl-domain/<domain-uuid>.mxl-domain"
```

> **Note:** `volume=0.1` ≈ −20 dBFS (`10^(−20/20)`). `wave=0` is a sine wave.
> `pattern=19` is the 100% colour bars (SMPTE full-range). Each flow gets a fresh `uuid4`
> generated at pipeline start — UUIDs are never reused across restarts.

### Explanation

Generates three independent live test flows — one video and two audio — with no external
source. The pipeline is user-controlled: the UI's **Setup** section configures domain, raster,
frame rate, and per-flow metadata; the pipeline only starts when the user clicks **Start**.

**Video branch** — `videotestsrc → capsfilter(res+fps) → timeoverlay → textoverlay
→ videoconvert → capsfilter(v210) → queue → mxlsink`

`videotestsrc` generates synthetic colour-bar frames at the selected raster
(1280×720, 1920×1080, or 3840×2160) and frame rate (24/25/29.97/30/50/59.94/60 fps).
A `timeoverlay` burns the running pipeline clock into the bottom-right corner; a
`textoverlay` overlays a configurable ident string at the top centre.
Both overlays are live-adjustable (no pipeline rebuild required).
`videoconvert` is mandatory before the v210 capsfilter: `timeoverlay` and `textoverlay`
work in AYUV or I420 internally, and `videoconvert` translates that into the packed v210
format that `mxlsink` requires. Without this explicit capsfilter, auto-negotiation may
land on an incompatible pixel format and cause a hard runtime failure.

**Audio Flow 1 and Audio Flow 2** — each independently:
`audiotestsrc → audioconvert → capsfilter(F32LE, N ch, 48 kHz) → queue → mxlsink`

Each branch has its own `audiotestsrc` (independently configurable wave, level) feeding
its own `mxlsink` through its own UUID — the two flows are completely independent.
`audiotestsrc` natively produces float samples at whatever rate and channel count the
downstream capsfilter negotiates; `audioconvert` is present to handle any intermediate
format differences and to make the negotiation chain explicit.
The final capsfilter locks the format to `F32LE` interleaved, which is the only audio
format `mxlsink` accepts. Channel count (1–64) is configured per flow in the Setup
section before the pipeline starts and is fixed for its lifetime — it cannot be changed
without a Stop + Start because caps renegotiation across a running mxlsink is not
reliable. Audio level (−60 … 0 dBFS in 0.5 dB steps) is adjustable live.

After the pipeline reaches PLAYING state, the backend polls for each flow's
`{domain}/{uuid}.mxl-flow/flow_def.json` and patches the `grouphint`, `description`,
and `label` fields with the values entered in the Setup form.

### Diagram

```mermaid
flowchart TB
    subgraph video_branch["Video branch"]
        direction LR
        vsrc["videotestsrc\npattern=100% bars\nis-live=true"]
        vcaps_src["capsfilter\n1920×1080, 30 fps"]
        time["timeoverlay\nclock, right-bottom"]
        text["textoverlay\nident, center-top"]
        vconv["videoconvert"]
        vcaps_sink["capsfilter\nformat=v210"]
        vqueue["queue"]
        vsink["mxlsink\nflow-id=video-uuid"]

        vsrc --> vcaps_src --> time --> text --> vconv --> vcaps_sink --> vqueue --> vsink
    end

    subgraph audio1_branch["Audio Flow 1 branch"]
        direction LR
        asrc1["audiotestsrc\nwave=sine, 1 kHz\n−20 dBFS, is-live=true"]
        aconv1["audioconvert"]
        acaps1["capsfilter\nF32LE, 2 ch, 48 kHz"]
        aqueue1["queue"]
        asink1["mxlsink\nflow-id=audio1-uuid"]

        asrc1 --> aconv1 --> acaps1 --> aqueue1 --> asink1
    end

    subgraph audio2_branch["Audio Flow 2 branch"]
        direction LR
        asrc2["audiotestsrc\nwave=sine, 1 kHz\n−20 dBFS, is-live=true"]
        aconv2["audioconvert"]
        acaps2["capsfilter\nF32LE, 2 ch, 48 kHz"]
        aqueue2["queue"]
        asink2["mxlsink\nflow-id=audio2-uuid"]

        asrc2 --> aconv2 --> acaps2 --> aqueue2 --> asink2
    end

    video_branch ~~~ audio1_branch ~~~ audio2_branch
```

---

## 3. HLS-to-MXL Gateway (`gst_hls.py`)

### `gst-launch-1.0` commands

**Phase 1 – Warmup (10 s)**

```bash
gst-launch-1.0 \
  uridecodebin uri="<HLS_URL>" connection-speed=50000 \
  ! fakesink sync=false
```

**Phase 2 – Real MXL pipeline**

```bash
gst-launch-1.0 \
  uridecodebin uri="<HLS_URL>" connection-speed=50000 name=dec \
  dec. ! videoconvert ! videoscale \
       ! "video/x-raw,width=1920,height=1080" \
       ! videorate \
       ! "video/x-raw,framerate=60/1" \
       ! videoconvert \
       ! "video/x-raw,format=v210,width=1920,height=1080,framerate=60/1" \
       ! queue \
       ! mxlsink flow-id="<video-flow-id>" domain="/mxl-domain" sync=false \
  dec. ! audioconvert ! audioresample \
       ! "audio/x-raw,format=F32LE,channels=2,rate=48000,layout=interleaved" \
       ! queue \
       ! mxlsink flow-id="<audio-flow-id>" domain="/mxl-domain" sync=false
```

### Explanation

Ingests an HLS stream and republishes it as two MXL flows.

**Two-phase startup** avoids delivering a low-quality variant to MXL during adaptive-bitrate
stabilisation.  During the 10-second warmup, `uridecodebin` connects to the HLS manifest with
`connection-speed=50000` (kbps) to force the highest bitrate variant, but the decoded frames go
to `fakesink` — the MXL writers are not yet involved. After the warmup the stale MXL flow
directories are deleted, and the real pipeline is launched.

The **video branch** normalises the incoming frames to 1920×1080 (via `videoscale`) and
60 fps (via `videorate`), then locks the pixel format to v210 before writing to `mxlsink`.
The double `videoconvert` pattern ensures `videoscale` and `videorate` always have a compatible
intermediate format regardless of what the HLS variant delivers.

The **audio branch** converts to F32LE stereo 48 kHz — the format `mxlsink` requires for audio.

On any GStreamer error the pipeline automatically tears down, cleans up the flow directories, and
restarts the full warmup sequence after 2 seconds.

### Diagram

```mermaid
flowchart LR
    subgraph phase1["Phase 1 – Warmup (10 s)"]
        w_src["uridecodebin\nuri=HLS_URL\nconnection-speed=50000"]
        w_fake["fakesink\nsync=false"]
        w_src -->|"decoded pads"| w_fake
    end

    subgraph phase2["Phase 2 – MXL Pipeline"]
        src["uridecodebin\nuri=HLS_URL\nconnection-speed=50000"]

        subgraph vbranch["Video branch"]
            vconv1["videoconvert"]
            vscale["videoscale"]
            vrcaps["capsfilter\n1920×1080"]
            vrate["videorate"]
            vfcaps["capsfilter\n60 fps"]
            vconv2["videoconvert"]
            vfinal["capsfilter\nv210, 1920×1080, 60 fps"]
            vqueue["queue"]
            vsink["mxlsink\nflow-id=video\nsync=false"]
            vconv1 --> vscale --> vrcaps --> vrate --> vfcaps --> vconv2 --> vfinal --> vqueue --> vsink
        end

        subgraph abranch["Audio branch"]
            aconv["audioconvert"]
            aresample["audioresample"]
            acaps["capsfilter\nF32LE, 2 ch, 48 kHz"]
            aqueue["queue"]
            asink["mxlsink\nflow-id=audio\nsync=false"]
            aconv --> aresample --> acaps --> aqueue --> asink
        end

        src -->|"video pad"| vconv1
        src -->|"audio pad"| aconv
    end

    phase1 -. "after 10 s teardown\n→ rebuild" .-> phase2
```

---

## 4. Input Selector (`gst_selector.py`)

### `gst-launch-1.0` command

**With connected slots (example with 3 inputs)**

```bash
gst-launch-1.0 \
  input-selector name=sel sync-streams=false \
  mxlsrc video-flow-id="<flow-id-1>" domain="/mxl-domain" \
       ! videoconvert ! queue leaky=1 max-size-buffers=3 ! sel.sink_0 \
  mxlsrc video-flow-id="<flow-id-2>" domain="/mxl-domain" \
       ! videoconvert ! queue leaky=1 max-size-buffers=3 ! sel.sink_1 \
  mxlsrc video-flow-id="<flow-id-3>" domain="/mxl-domain" \
       ! videoconvert ! queue leaky=1 max-size-buffers=3 ! sel.sink_2 \
  sel. ! videoconvert \
       ! "video/x-raw,format=v210" \
       ! queue \
       ! mxlsink flow-id="<output-flow-id>" domain="/mxl-domain" sync=false
```

**When no input is connected (black output fallback)**

```bash
gst-launch-1.0 \
  videotestsrc pattern=2 is-live=true \
  ! videoconvert \
  ! "video/x-raw,format=v210" \
  ! mxlsink flow-id="<output-flow-id>" domain="/mxl-domain" sync=false
```

### Explanation

Implements a **2-step vision mixer switch**: a `select` call pre-arms the desired input,
and a `take` call atomically switches the output — matching the traditional on-air take paradigm.

Up to three `mxlsrc` sources (NMOS receivers, slots 1–3) feed an `input-selector` element.
Each branch has a small leaky queue (`leaky=1` drops the oldest frame on overflow) to decouple
the sources from the selector without building up latency.  The selector's single output feeds a
`videoconvert` and then `mxlsink`.

When no slot is connected the `input-selector` is bypassed entirely and a black
`videotestsrc` is linked directly to the output sink, keeping the MXL flow alive.
The pipeline is rebuilt whenever a slot is connected or disconnected; the active pad selection
is restored after each rebuild.

### Diagram

```mermaid
flowchart LR
    subgraph "Slot 1"
        s1["mxlsrc\nflow-id=slot1"] --> c1["videoconvert"] --> q1["queue\nleaky=drop-oldest\nmax=3"]
    end
    subgraph "Slot 2"
        s2["mxlsrc\nflow-id=slot2"] --> c2["videoconvert"] --> q2["queue\nleaky=drop-oldest\nmax=3"]
    end
    subgraph "Slot 3"
        s3["mxlsrc\nflow-id=slot3"] --> c3["videoconvert"] --> q3["queue\nleaky=drop-oldest\nmax=3"]
    end

    q1 -->|"sink_0"| sel["input-selector\nsync-streams=false"]
    q2 -->|"sink_1"| sel
    q3 -->|"sink_2"| sel

    sel --> oconv["videoconvert"] --> ocaps["capsfilter\nformat=v210"] --> oqueue["queue"] --> osink["mxlsink\nflow-id=output\nsync=false"]

    black["videotestsrc\npattern=black\n(fallback: no slots)"] -.->|"bypass selector"| oconv
```

---

## 5. HTML5 Keyer (`gst_keyer.py`)

### `gst-launch-1.0` command

```bash
gst-launch-1.0 \
  compositor name=mixer \
  mxlsrc video-flow-id="<input-flow-id>" domain="/mxl-domain" \
       ! videoconvert \
       ! "video/x-raw,format=BGRA" \
       ! queue leaky=2 max-size-buffers=2 max-size-time=0 max-size-bytes=0 \
       ! mixer.sink_0 \
  cefsrc url="http://spx-server:5660/renderer/" \
       ! "video/x-raw,format=BGRA,width=1920,height=1080,framerate=60/1" \
       ! videorate \
       ! queue leaky=2 max-size-buffers=2 max-size-time=0 max-size-bytes=0 \
       ! mixer.sink_1 \
  mixer. ! videoconvert \
       ! "video/x-raw,format=v210,width=1920,height=1080,framerate=60/1" \
       ! queue leaky=2 max-size-buffers=2 \
       ! mxlsink flow-id="<output-flow-id>" domain="/mxl-domain" sync=false
```

> **Key toggle:** adjust the `alpha` property on `mixer.sink_1` at runtime
> (`0.0` = key OFF, `1.0` = key ON) without rebuilding the pipeline.
>
> **`sync=false` on `mixer.sink_1`:** set programmatically via the element API — not
> expressible in a `gst-launch` capsfilter; in the Python code it is set directly on the
> `Gst.Pad` object returned by `mixer.get_request_pad("sink_%u")`.

### Explanation

Composites an HTML5 graphics overlay (rendered by a Chrome/CEF browser pointed at an SPX
graphics server) on top of a live MXL video background using CPU-based mixing via `compositor`.

The **background branch** reads an MXL flow (or falls back to a black `videotestsrc` when no
receiver is connected). `videoconvert` converts v210 to BGRA so that `compositor` receives the
same pixel format on both pads without internal conversion. The queue is leaky (drop-oldest, 2
frames) — mandatory for a live pipeline to avoid buffering stale frames that would appear as
repeated or frozen video.

The **CEF overlay branch** connects `cefsrc` to the SPX renderer URL which delivers the HTML5
graphic in BGRA format (alpha channel preserved for keying).  A `videorate` element guarantees
a steady 60 fps feed to the mixer even when the browser renders at a lower rate.

`compositor` (CPU-based) is used instead of `glvideomixer` (GPU) for two reasons:
- Its sink pads support `sync=false` — `sink_1` (CEF) is set to `sync=false` so the mixer
  composites immediately without stalling to align CEF timestamps with the mxlsrc clock domain.
- No `glupload`/`gldownload` round-trip, saving ~950 MB/s of GPU memory bandwidth.

Toggling the key simply sets the `alpha` property on `sink_1` (the CEF pad) between `0.0`
and `1.0` — no pipeline rebuild needed.

The composited output is converted to v210 and written to `mxlsink`.
The pipeline is only rebuilt when the MXL receiver connection changes.

### Diagram

```mermaid
flowchart LR
    subgraph "Background branch"
        bg["mxlsrc\n(or videotestsrc black\nif no input)"]
        bg_conv["videoconvert\n→ BGRA"]
        bg_icaps["capsfilter\nformat=BGRA"]
        bg_q["queue\nleaky=drop-oldest, max=2"]
        bg --> bg_conv --> bg_icaps --> bg_q
    end

    subgraph "CEF overlay branch"
        cef["cefsrc\nurl=spx-server/renderer/"]
        cef_caps["capsfilter\nBGRA, 1920×1080, 60 fps"]
        cef_rate["videorate"]
        cef_q["queue\nleaky=drop-oldest, max=2"]
        cef --> cef_caps --> cef_rate --> cef_q
    end

    mixer["compositor\n(CPU composite)"]
    out_conv["videoconvert"]
    out_caps["capsfilter\nv210, 1920×1080, 60 fps"]
    out_q["queue\nleaky=drop-oldest, max=2"]
    out_sink["mxlsink\nflow-id=output\nsync=false"]

    bg_q   -->|"sink_0"| mixer
    cef_q  -->|"sink_1\nsync=false\nalpha = 0.0 (OFF)\nor 1.0 (ON)"| mixer

    mixer --> out_conv --> out_caps --> out_q --> out_sink
```

---

## 6. MXL-to-WebRTC (`gst_mxl2webrtc.py`)

### `gst-launch-1.0` command

```bash
gst-launch-1.0 \
  webrtcsink name=ws \
  mxlsrc video-flow-id="<video-flow-id>" domain="/mxl-domain" \
       ! videoconvert ! videoscale \
       ! "video/x-raw,format=I420,width=1280,height=720" \
       ! x264enc tune=4 speed-preset=5 bitrate=6000 key-int-max=60 \
       ! h264parse ! queue \
       ! ws.video_0 \
  mxlsrc audio-flow-id="<audio-flow-id>" domain="/mxl-domain" \
       ! audioconvert ! audioresample \
       ! "audio/x-raw,format=S16LE,channels=2,rate=48000,layout=interleaved" \
       ! queue \
       ! ws.audio_0
```

> **Signaller:** the `webrtcsink` signaller URI is set to `ws://127.0.0.1:8443/` at runtime
> (not expressible as a `gst-launch` property; requires the element API).
> `tune=4` is the `zerolatency` flag bitmask for x264enc.

### Explanation

Transcodes two MXL flows (video + audio) and delivers them to WebRTC consumers via a
local signalling server.

The **video branch** reads the MXL video flow, scales it down from 1080p to 720p (1280×720)
to reduce bandwidth, and encodes it with `x264enc` using the `zerolatency` tune for minimum
latency, a `fast` speed preset for balanced quality/CPU, 6 Mbps target bitrate, and a keyframe
every 60 frames (1 second at 60 fps).  `h264parse` re-frames the bitstream into a form
`webrtcsink` can consume.

The **audio branch** converts the MXL audio to S16LE stereo 48 kHz — the standard PCM format
expected by WebRTC before Opus encoding inside `webrtcsink`.

`webrtcsink` handles SDP negotiation, ICE, DTLS-SRTP, and RTP packetisation internally.
It accepts pre-encoded H.264 video (declared via `video-caps`) so the bitstream from `x264enc`
is passed through without re-encoding.

A generation counter guards against stale bus-error callbacks after a pipeline rebuild.
Transient startup errors trigger up to 3 automatic retries (2 s apart) to handle `mxlsrc`
ring-buffer availability races.

### Diagram

```mermaid
flowchart LR
    subgraph "Video branch"
        vsrc["mxlsrc\nvideo-flow-id"]
        vconv["videoconvert"]
        vscale["videoscale"]
        vcaps["capsfilter\nI420, 1280×720"]
        venc["x264enc\n6 Mbps, zerolatency\nspeed-preset=fast\nkeyframe every 60"]
        vparse["h264parse"]
        vqueue["queue"]
        vsrc --> vconv --> vscale --> vcaps --> venc --> vparse --> vqueue
    end

    subgraph "Audio branch"
        asrc["mxlsrc\naudio-flow-id"]
        aconv["audioconvert"]
        aresample["audioresample"]
        acaps["capsfilter\nS16LE, 2 ch, 48 kHz"]
        aqueue["queue"]
        asrc --> aconv --> aresample --> acaps --> aqueue
    end

    ws["webrtcsink\nsignaller=ws://127.0.0.1:8443/\nvideo-caps=video/x-h264"]

    vqueue -->|"video_0"| ws
    aqueue -->|"audio_0"| ws

    ws -->|"WebRTC / SDP+ICE"| browser["Browser\n(WebRTC consumer)"]
```
