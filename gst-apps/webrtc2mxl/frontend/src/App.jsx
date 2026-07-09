// SPDX-FileCopyrightText: 2025 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import React, { useState, useEffect, useCallback, useRef } from "react";

const API = "";

// ── Styles ──────────────────────────────────────────────────────────────────

const S = {
  card: {
    background: "#1c1c1c",
    borderRadius: "8px",
    padding: "1.25rem 1.5rem",
    marginBottom: "1rem",
  },
  sectionTitle: {
    fontSize: "0.7rem",
    fontWeight: 700,
    letterSpacing: "0.12em",
    color: "#666",
    textTransform: "uppercase",
    marginBottom: "0.75rem",
  },
  label: {
    display: "block",
    marginBottom: "0.3rem",
    color: "#aaa",
    fontSize: "0.82rem",
  },
  select: {
    width: "100%",
    padding: "0.45rem 0.6rem",
    background: "#2a2a2a",
    color: "#fff",
    border: "1px solid #444",
    borderRadius: "4px",
    fontSize: "0.95rem",
    boxSizing: "border-box",
  },
  row: { display: "flex", gap: "0.75rem", alignItems: "flex-end" },
  col: { flex: 1 },
};

const btn = (variant = "primary", disabled = false) => ({
  padding: "0.5rem 1.25rem",
  borderRadius: "4px",
  border: "none",
  cursor: disabled ? "not-allowed" : "pointer",
  fontWeight: 600,
  fontSize: "0.95rem",
  opacity: disabled ? 0.45 : 1,
  background:
    variant === "danger"  ? "#8b1a1a" :
    variant === "success" ? "#0d7c3e" :
    "#2a5caa",
  color: "#fff",
});

const badge = (running) => ({
  display: "inline-block",
  padding: "0.2rem 0.65rem",
  borderRadius: "20px",
  background: running ? "#1a5c2a" : "#3a3a3a",
  color:      running ? "#4caf50" : "#888",
  fontSize: "0.75rem",
  fontWeight: 700,
  marginLeft: "0.75rem",
  verticalAlign: "middle",
});

const connDot = (active) => ({
  display: "inline-block",
  width: "10px",
  height: "10px",
  borderRadius: "50%",
  background: active ? "#4caf50" : "#444",
  marginRight: "8px",
  flexShrink: 0,
});

const disabledOverlay = (disabled) => disabled ? { opacity: 0.4, pointerEvents: "none" } : {};

function waitIceGathering(pc, timeoutMs = 5000) {
  return new Promise((resolve) => {
    if (pc.iceGatheringState === "complete") { resolve(); return; }
    const t = setTimeout(resolve, timeoutMs);
    pc.onicegatheringstatechange = () => {
      if (pc.iceGatheringState === "complete") { clearTimeout(t); resolve(); }
    };
  });
}

// ── Microphone WHIP publisher ─────────────────────────────────────────────────
// Captures the selected mic, publishes it to MediaMTX over WHIP (send-only Opus),
// and drives a level meter from the captured stream. The GStreamer backend then
// pulls the same MediaMTX path via WHEP and writes the MXL flow.
function useMicPublisher(meterRef) {
  const pcRef       = useRef(null);
  const streamRef   = useRef(null);
  const resourceRef = useRef(null);   // WHIP resource URL (Location) for DELETE
  const audioCtxRef = useRef(null);
  const rafRef      = useRef(null);
  const [state, setState] = useState("idle");   // idle | publishing | live | error
  const [error, setError] = useState(null);

  const startMeter = useCallback((stream) => {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const ctx = new AudioCtx();
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);   // not connected to destination — no echo
      const data = new Uint8Array(analyser.fftSize);
      const loop = () => {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const x = (data[i] - 128) / 128;
          sum += x * x;
        }
        const rms = Math.sqrt(sum / data.length);
        const pct = Math.min(100, rms * 200);
        if (meterRef.current) meterRef.current.style.width = pct + "%";
        rafRef.current = requestAnimationFrame(loop);
      };
      loop();
    } catch (err) {
      // The meter is non-essential; a failure here must not break publishing.
      console.warn("level meter unavailable:", err);
    }
  }, [meterRef]);

  const stopMeter = useCallback(() => {
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    if (audioCtxRef.current) { audioCtxRef.current.close().catch(() => {}); audioCtxRef.current = null; }
    if (meterRef.current) meterRef.current.style.width = "0%";
  }, [meterRef]);

  const stop = useCallback(() => {
    if (resourceRef.current) {
      try { fetch(resourceRef.current, { method: "DELETE", keepalive: true }).catch(() => {}); } catch {}
      resourceRef.current = null;
    }
    if (pcRef.current) {
      pcRef.current.onconnectionstatechange = null;
      pcRef.current.close();
      pcRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    stopMeter();
    setState("idle");
    setError(null);
  }, [stopMeter]);

  // Publish the mic to `whipUrl`. Resolves on success, throws on failure.
  const start = useCallback(async (whipUrl, deviceId) => {
    setState("publishing");
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: deviceId ? { deviceId: { exact: deviceId } } : true,
      });
      streamRef.current = stream;
      startMeter(stream);

      const pc = new RTCPeerConnection({ iceServers: [] });
      pcRef.current = pc;
      stream.getAudioTracks().forEach(t => pc.addTrack(t, stream));

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") setState("live");
        else if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
          setState("error");
          setError("WebRTC connection lost");
        }
      };

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitIceGathering(pc);

      const resp = await fetch(whipUrl, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: pc.localDescription.sdp,
      });
      if (!resp.ok) throw new Error(`WHIP publish failed (HTTP ${resp.status})`);

      const loc = resp.headers.get("Location");
      if (loc) { try { resourceRef.current = new URL(loc, whipUrl).href; } catch { resourceRef.current = loc; } }

      const answerSdp = await resp.text();
      await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
    } catch (err) {
      stop();
      setState("error");
      setError(err.message || String(err));
      throw err;
    }
  }, [startMeter, stop]);

  // Tear down on unmount.
  useEffect(() => stop, [stop]);

  return { state, error, start, stop };
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [whipUrl, setWhipUrl]               = useState(null);
  const [domains, setDomains]               = useState([]);
  const [selectedDomain, setSelectedDomain] = useState(null);
  const [mics, setMics]                     = useState([]);
  const [selectedMic, setSelectedMic]       = useState("");
  const [grouphint, setGrouphint]           = useState("WEBRTC2MXL");
  const [label, setLabel]                   = useState("webrtc-audio");
  const [description, setDescription]       = useState("webrtc-audio-out");
  const [status, setStatus]                 = useState(null);
  const [starting, setStarting]             = useState(false);

  const meterRef = useRef(null);
  const { state: pubState, error: pubError, start: publish, stop: unpublish } = useMicPublisher(meterRef);

  const running = status?.running === true;

  // Enumerate audio input devices (labels populate after permission is granted).
  const loadMics = useCallback(() => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    navigator.mediaDevices.enumerateDevices()
      .then(devs => {
        const inputs = devs.filter(d => d.kind === "audioinput");
        setMics(inputs);
        setSelectedMic(prev => prev || (inputs[0]?.deviceId ?? ""));
      })
      .catch(() => {});
  }, []);

  // Fetch config + domains, enumerate mics on mount.
  useEffect(() => {
    fetch(`${API}/config`)
      .then(r => r.json())
      .then(d => {
        // Keep the path/port from config but substitute the browser's own
        // hostname, so the WHIP URL works locally or from a remote machine.
        const cfg = new URL(d.mediamtx_whip_url);
        setWhipUrl(`${cfg.protocol}//${window.location.hostname}:${cfg.port || "8889"}${cfg.pathname}`);
      })
      .catch(() => {});
    fetch(`${API}/domains`)
      .then(r => r.json())
      .then(d => { if (Array.isArray(d)) setDomains(d); })
      .catch(() => {});
    // Prime microphone permission so the device list shows real labels/ids and
    // the selector is usable. If denied, fall back to a plain (generic) enumerate.
    (async () => {
      try {
        const s = await navigator.mediaDevices.getUserMedia({ audio: true });
        s.getTracks().forEach(t => t.stop());
      } catch { /* permission denied — selector will show a generic entry */ }
      loadMics();
    })();
    navigator.mediaDevices?.addEventListener?.("devicechange", loadMics);
    return () => navigator.mediaDevices?.removeEventListener?.("devicechange", loadMics);
  }, [loadMics]);

  // Poll pipeline status every 2 s.
  useEffect(() => {
    const poll = () =>
      fetch(`${API}/pipeline/status`).then(r => r.json()).then(setStatus).catch(() => {});
    poll();
    const id = setInterval(poll, 2000);
    return () => clearInterval(id);
  }, []);

  const rescanDomains = () =>
    fetch(`${API}/get-domains`, { method: "POST" })
      .then(r => r.json())
      .then(d => { if (Array.isArray(d)) setDomains(d); })
      .catch(() => {});

  const handleStart = async () => {
    if (!whipUrl) return;
    setStarting(true);
    try {
      // 1) Publish the mic to MediaMTX FIRST so the WHEP path has a live source.
      await publish(whipUrl, selectedMic);
      // Permission is now granted — refresh device labels.
      loadMics();
      // 2) Start the GStreamer WHEP→mxlsink pipeline.
      const r = await fetch(`${API}/pipeline/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          domain_path: selectedDomain,
          grouphint:   grouphint.trim(),
          label:       label.trim(),
          description: description.trim(),
        }),
      });
      setStatus(await r.json());
    } catch {
      // publish() already surfaced the error; nothing more to start.
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    await fetch(`${API}/pipeline/stop`, { method: "POST" }).catch(() => {});
    unpublish();
    fetch(`${API}/pipeline/status`).then(r => r.json()).then(setStatus).catch(() => {});
  };

  // Note: a specific mic is not required — getUserMedia falls back to the default
  // input when no deviceId is chosen (deviceIds are empty before permission).
  const canStart = !running && !starting && !!selectedDomain && !!whipUrl
    && !!grouphint.trim() && !!label.trim() && !!description.trim();

  const micLabel = (m, i) => m.label || `Microphone ${i + 1}`;

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1.5rem" }}>
        <img src="/cbc-logo.png" alt="CBC Radio-Canada" style={{ height: "2.2rem" }} />
        <h1 style={{ fontSize: "1.6rem", fontWeight: 700 }}>
          WebRTC to MXL
          <span style={badge(running)}>
            {running ? "● RUNNING" : "○ STOPPED"}
          </span>
        </h1>
      </div>

      {/* ── Section 1: Setup ───────────────────────────────────────────── */}
      <div style={S.card}>
        <div style={S.sectionTitle}>1 — Setup</div>

        <div style={disabledOverlay(running)}>
          {/* Domain row */}
          <div style={{ ...S.row, marginBottom: "0.75rem" }}>
            <div style={S.col}>
              <label style={S.label}>MXL Domain</label>
              <select
                style={S.select}
                value={selectedDomain || ""}
                onChange={e => setSelectedDomain(e.target.value || null)}
                disabled={running}
              >
                <option value="">— select a domain —</option>
                {domains.map(d => (
                  <option key={d.path} value={d.path}>{d.label || d.path}</option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ ...S.label, visibility: "hidden" }}>.</label>
              <button style={btn("primary", running)} onClick={rescanDomains} disabled={running}>
                Scan Domains
              </button>
            </div>
          </div>

          {/* Microphone + group hint row */}
          <div style={{ ...S.row, marginBottom: "0.75rem" }}>
            <div style={S.col}>
              <label style={S.label}>Microphone</label>
              <select
                style={S.select}
                value={selectedMic}
                onChange={e => setSelectedMic(e.target.value)}
                disabled={running}
              >
                {mics.length === 0 && <option value="">— no microphone found —</option>}
                {mics.map((m, i) => (
                  <option key={m.deviceId || i} value={m.deviceId}>{micLabel(m, i)}</option>
                ))}
              </select>
            </div>
            <div style={S.col}>
              <label style={S.label}>Group Hint</label>
              <input
                type="text"
                style={S.select}
                value={grouphint}
                onChange={e => setGrouphint(e.target.value)}
                disabled={running}
              />
            </div>
          </div>

          {/* Output flow: label + description (audio flow) */}
          <div style={{ ...S.row, marginBottom: "0.25rem" }}>
            <div style={S.col}>
              <label style={S.label}>Label</label>
              <input
                type="text"
                style={S.select}
                value={label}
                onChange={e => setLabel(e.target.value)}
                disabled={running}
                placeholder="label"
              />
            </div>
            <div style={S.col}>
              <label style={S.label}>Description</label>
              <input
                type="text"
                style={S.select}
                value={description}
                onChange={e => setDescription(e.target.value)}
                disabled={running}
                placeholder="description"
              />
            </div>
          </div>
        </div>

        {/* Start / Stop button — outside the disabled overlay so it always works */}
        <div style={{ marginTop: "0.75rem" }}>
          {!running ? (
            <button style={btn("success", !canStart)} onClick={handleStart} disabled={!canStart}>
              {starting ? "Starting…" : "Start"}
            </button>
          ) : (
            <button style={btn("danger")} onClick={handleStop}>Stop</button>
          )}
          <p style={{ color: "#666", fontSize: "0.72rem", margin: "0.5rem 0 0" }}>
            Captures the selected microphone, publishes it to MediaMTX over WHIP, and writes an MXL
            audio flow. Microphone access requires a secure context (localhost is fine; a remote
            host needs HTTPS).
          </p>
        </div>
      </div>

      {/* ── Section 2: Operation ───────────────────────────────────────── */}
      <div style={{ ...S.card, ...disabledOverlay(!running) }}>
        <div style={S.sectionTitle}>2 — Operation</div>

        {/* MXL output status */}
        <div style={{ marginBottom: "1rem" }}>
          <div style={{ ...S.label, marginBottom: "0.4rem" }}>MXL Output</div>
          <div style={{ display: "flex", alignItems: "center", fontSize: "0.85rem", marginBottom: "0.35rem" }}>
            <span style={connDot(!!status?.flow_uuid)} />
            <span style={{ color: "#aaa", width: "62px" }}>Label</span>
            <span style={{ color: status?.label ? "#ccc" : "#555" }}>
              {status?.label || "—"}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", fontSize: "0.85rem", marginBottom: "0.35rem" }}>
            <span style={connDot(!!status?.flow_uuid)} />
            <span style={{ color: "#aaa", width: "62px" }}>Group</span>
            <span style={{ color: status?.grouphint ? "#ccc" : "#555" }}>
              {status?.grouphint ? `${status.grouphint}:Audio` : "—"}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", fontSize: "0.85rem" }}>
            <span style={connDot(!!status?.flow_uuid)} />
            <span style={{ color: "#aaa", width: "62px" }}>UUID</span>
            <span style={{ fontFamily: "monospace", color: status?.flow_uuid ? "#ccc" : "#555" }}>
              {status?.flow_uuid || "—"}
            </span>
          </div>
        </div>

        {/* Connection status */}
        <div style={{ ...S.label, marginBottom: "0.5rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span>Microphone → MediaMTX (WHIP)</span>
          {pubState === "live" && <span style={{ ...badge(true), fontSize: "0.7rem" }}>● LIVE</span>}
          {pubState === "publishing" && <span style={{ color: "#888", fontSize: "0.75rem" }}>connecting…</span>}
          {pubState === "error" && <span style={{ color: "#f44336", fontSize: "0.75rem" }}>error</span>}
        </div>

        {/* Mic level meter */}
        <div style={{ marginBottom: "0.5rem" }}>
          <div style={{ ...S.label, marginBottom: "0.3rem" }}>Microphone level</div>
          <div style={{ background: "#0a0a0a", borderRadius: "5px", height: "18px", overflow: "hidden" }}>
            <div ref={meterRef} style={{
              width: "0%", height: "100%",
              background: "linear-gradient(90deg, #2e7d32, #66bb6a, #ffca28)",
              transition: "width 0.05s linear",
            }} />
          </div>
        </div>

        <p style={{ color: "#444", fontSize: "0.72rem", marginTop: "0.4rem" }}>
          Publishing to {whipUrl ?? "…"}
        </p>
      </div>

      {/* Publish error banner */}
      {pubError && (
        <div style={{ ...S.card, background: "#2a0a0a", border: "1px solid #5c1a1a" }}>
          <span style={{ color: "#f44336", fontSize: "0.85rem" }}>
            Microphone / WHIP error: {pubError}
          </span>
        </div>
      )}

      {/* Pipeline error banner */}
      {status?.error && (
        <div style={{ ...S.card, background: "#2a0a0a", border: "1px solid #5c1a1a" }}>
          <span style={{ color: "#f44336", fontSize: "0.85rem" }}>
            Pipeline error: {status.error}
          </span>
        </div>
      )}
    </div>
  );
}
