// SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
// SPDX-License-Identifier: Apache-2.0
import React, { useState, useEffect, useCallback, useRef } from "react";

const API = "";

// ── Styles ────────────────────────────────────────────────────────────────────

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

// ── Flow option helpers ───────────────────────────────────────────────────────

function flowOptionLabel(f) {
  const prefix = f.flow_uuid.slice(0, 8);
  const desc   = f.description || "";
  const label  = f.flow_label  || "";
  const gh     = f.flow_grouphint || "";
  return `(${prefix}…) ${desc || label}${desc && label && desc !== label ? ` — ${label}` : ""} [${gh}]`;
}

function flowRole(f) {
  const parts = (f.flow_grouphint || "").split(":");
  return parts.length > 1 ? parts.slice(1).join(":").trim().toLowerCase() : "";
}

const isVideoFlow = (f) => flowRole(f).includes("video");
const isAudioFlow = (f) => flowRole(f).includes("audio");

// ── WHEP player hook ──────────────────────────────────────────────────────────

function useWhepPlayer(pipelinePlaying, mediamtxUrl) {
  const videoRef = useRef(null);
  const pcRef    = useRef(null);
  const [playerState, setPlayerState] = useState("idle");
  const [playerError, setPlayerError] = useState(null);

  const cleanup = useCallback(() => {
    if (pcRef.current) {
      pcRef.current.ontrack = null;
      pcRef.current.onconnectionstatechange = null;
      pcRef.current.close();
      pcRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  useEffect(() => {
    if (!pipelinePlaying || !mediamtxUrl) {
      cleanup();
      setPlayerState("idle");
      setPlayerError(null);
      return;
    }

    let cancelled = false;
    setPlayerState("connecting");
    setPlayerError(null);

    const whepUrl = `${mediamtxUrl}/mxl2webrtc/whep`;

    const connect = async () => {
      for (let i = 0; i < 12; i++) {
        if (cancelled) return;
        try {
          const pc = new RTCPeerConnection({ iceServers: [] });
          pcRef.current = pc;

          pc.addTransceiver("video", { direction: "recvonly" });
          pc.addTransceiver("audio", { direction: "recvonly" });

          pc.ontrack = (e) => {
            if (cancelled) return;
            if (videoRef.current && e.streams[0]) {
              videoRef.current.srcObject = e.streams[0];
              setPlayerState("playing");
            }
          };

          pc.onconnectionstatechange = () => {
            if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
              setPlayerState("error");
              setPlayerError("WebRTC connection lost");
            }
          };

          const offer = await pc.createOffer();
          await pc.setLocalDescription(offer);

          // Wait for ICE gathering before sending the offer (avoids trickle ICE)
          await new Promise((resolve) => {
            if (pc.iceGatheringState === "complete") { resolve(); return; }
            const t = setTimeout(resolve, 5000);
            pc.onicegatheringstatechange = () => {
              if (pc.iceGatheringState === "complete") { clearTimeout(t); resolve(); }
            };
          });

          if (cancelled) { pc.close(); return; }

          const resp = await fetch(whepUrl, {
            method: "POST",
            headers: { "Content-Type": "application/sdp" },
            body: pc.localDescription.sdp,
          });

          if (!resp.ok) {
            pc.close();
            pcRef.current = null;
            // 404 means MediaMTX has no publisher yet — retry
            if (i < 11) { await new Promise(r => setTimeout(r, 2000)); continue; }
            setPlayerState("error");
            setPlayerError(`WHEP error ${resp.status}`);
            return;
          }

          const answerSdp = await resp.text();
          await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
          return; // success

        } catch (err) {
          if (pcRef.current) { pcRef.current.close(); pcRef.current = null; }
          if (!cancelled && i < 11) { await new Promise(r => setTimeout(r, 2000)); continue; }
          if (!cancelled) { setPlayerState("error"); setPlayerError(err.message); }
          return;
        }
      }
    };

    // Give GStreamer a moment to announce to MediaMTX before the first WHEP attempt
    const t = setTimeout(connect, 1500);
    return () => {
      cancelled = true;
      clearTimeout(t);
      cleanup();
      setPlayerState("idle");
    };
  }, [pipelinePlaying, mediamtxUrl, cleanup]);

  return { videoRef, playerState, playerError };
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [mediamtxUrl, setMediamtxUrl]       = useState(null);
  const [domains, setDomains]               = useState([]);
  const [selectedDomain, setSelectedDomain] = useState(null);
  const [flows, setFlows]                   = useState([]);
  const [flowsLoading, setFlowsLoading]     = useState(false);
  const [videoFlowUuid, setVideoFlowUuid]   = useState("none");
  const [audioFlowUuid, setAudioFlowUuid]   = useState("none");
  const [status, setStatus]                 = useState(null);
  const [starting, setStarting]             = useState(false);

  const running = status?.running === true;

  // Fetch config + cached domains on mount
  useEffect(() => {
    fetch(`${API}/config`)
      .then(r => r.json())
      .then(d => setMediamtxUrl(d.mediamtx_webrtc_url))
      .catch(() => {});
    fetch(`${API}/domains`)
      .then(r => r.json())
      .then(d => { if (Array.isArray(d)) setDomains(d); })
      .catch(() => {});
  }, []);

  // Poll pipeline status every 2 s
  useEffect(() => {
    const poll = () =>
      fetch(`${API}/pipeline/status`).then(r => r.json()).then(setStatus).catch(() => {});
    poll();
    const id = setInterval(poll, 2000);
    return () => clearInterval(id);
  }, []);

  // Load flows for selected domain
  const loadFlows = useCallback((path) => {
    if (!path) { setFlows([]); return; }
    setFlowsLoading(true);
    fetch(`${API}/scan-domain?domain_path=${encodeURIComponent(path)}`)
      .then(r => r.json())
      .then(d => setFlows(Array.isArray(d) ? d : []))
      .catch(() => setFlows([]))
      .finally(() => setFlowsLoading(false));
  }, []);

  const handleDomainChange = (path) => {
    setSelectedDomain(path || null);
    setVideoFlowUuid("none");
    setAudioFlowUuid("none");
    loadFlows(path || null);
  };

  const rescanDomains = () =>
    fetch(`${API}/get-domains`, { method: "POST" })
      .then(r => r.json())
      .then(d => { if (Array.isArray(d)) setDomains(d); })
      .catch(() => {});

  const refreshFlows = () => { if (selectedDomain) loadFlows(selectedDomain); };

  const handleStart = async () => {
    setStarting(true);
    try {
      const r = await fetch(`${API}/pipeline/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          domain_path:     selectedDomain,
          video_flow_uuid: videoFlowUuid !== "none" ? videoFlowUuid : null,
          audio_flow_uuid: audioFlowUuid !== "none" ? audioFlowUuid : null,
        }),
      });
      setStatus(await r.json());
    } catch {
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    await fetch(`${API}/pipeline/stop`, { method: "POST" }).catch(() => {});
    fetch(`${API}/pipeline/status`).then(r => r.json()).then(setStatus).catch(() => {});
  };

  const videoFlows = flows.filter(isVideoFlow);
  const audioFlows = flows.filter(isAudioFlow);
  const canStart   = !running && !starting && !!selectedDomain && (videoFlowUuid !== "none" || audioFlowUuid !== "none");

  const { videoRef, playerState, playerError } = useWhepPlayer(running, mediamtxUrl);

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1.5rem" }}>
        <img src="/cbc-logo.png" alt="CBC Radio-Canada" style={{ height: "2.2rem" }} />
        <h1 style={{ fontSize: "1.6rem", fontWeight: 700 }}>
          MXL to WebRTC
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
                onChange={e => handleDomainChange(e.target.value)}
                disabled={running}
              >
                <option value="">— select a domain —</option>
                {domains.map(d => (
                  <option key={d.path} value={d.path}>
                    {d.path}  ({(d.id || "").slice(0, 8)}…)
                  </option>
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

          {/* Flow selectors row */}
          <div style={{ ...S.row, marginBottom: "0.75rem" }}>
            <div style={S.col}>
              <label style={S.label}>
                Video Flow
                {flowsLoading && <span style={{ color: "#555", marginLeft: "0.4rem" }}>(loading…)</span>}
              </label>
              <select
                style={S.select}
                value={videoFlowUuid}
                onChange={e => setVideoFlowUuid(e.target.value)}
                disabled={running || !selectedDomain}
              >
                <option value="none">None — video disabled</option>
                {videoFlows.map(f => (
                  <option key={f.flow_uuid} value={f.flow_uuid}>{flowOptionLabel(f)}</option>
                ))}
              </select>
            </div>
            <div style={S.col}>
              <label style={S.label}>
                Audio Flow
                {flowsLoading && <span style={{ color: "#555", marginLeft: "0.4rem" }}>(loading…)</span>}
              </label>
              <select
                style={S.select}
                value={audioFlowUuid}
                onChange={e => setAudioFlowUuid(e.target.value)}
                disabled={running || !selectedDomain}
              >
                <option value="none">None — audio disabled</option>
                {audioFlows.map(f => (
                  <option key={f.flow_uuid} value={f.flow_uuid}>{flowOptionLabel(f)}</option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ ...S.label, visibility: "hidden" }}>.</label>
              <button
                style={btn("primary", running || !selectedDomain)}
                onClick={refreshFlows}
                disabled={running || !selectedDomain}
              >
                Refresh Flows
              </button>
            </div>
          </div>
        </div>

        {/* Start / Stop button — outside disabled overlay so it always works */}
        <div style={{ marginTop: "0.5rem" }}>
          {!running ? (
            <button style={btn("success", !canStart)} onClick={handleStart} disabled={!canStart}>
              {starting ? "Starting…" : "Start"}
            </button>
          ) : (
            <button style={btn("danger")} onClick={handleStop}>Stop</button>
          )}
        </div>
      </div>

      {/* ── Section 2: Operation ───────────────────────────────────────── */}
      <div style={{ ...S.card, ...disabledOverlay(!running) }}>
        <div style={S.sectionTitle}>2 — Operation</div>

        {/* MXL input status */}
        <div style={{ marginBottom: "1rem" }}>
          <div style={{ ...S.label, marginBottom: "0.4rem" }}>MXL Input</div>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
            <div style={{ display: "flex", alignItems: "center", fontSize: "0.85rem" }}>
              <span style={connDot(!!status?.video_flow_uuid)} />
              <span style={{ color: "#aaa", width: "52px" }}>Video</span>
              <span style={{ fontFamily: "monospace", color: status?.video_flow_uuid ? "#ccc" : "#555" }}>
                {status?.video_flow_uuid || "—"}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", fontSize: "0.85rem" }}>
              <span style={connDot(!!status?.audio_flow_uuid)} />
              <span style={{ color: "#aaa", width: "52px" }}>Audio</span>
              <span style={{ fontFamily: "monospace", color: status?.audio_flow_uuid ? "#ccc" : "#555" }}>
                {status?.audio_flow_uuid || "—"}
              </span>
            </div>
          </div>
        </div>

        {/* WebRTC player */}
        <div style={{ ...S.label, marginBottom: "0.5rem" }}>
          WebRTC Player
          {playerState === "playing" && (
            <span style={{ ...badge(true), fontSize: "0.7rem" }}>● LIVE</span>
          )}
          {playerState === "connecting" && (
            <span style={{ color: "#888", fontSize: "0.75rem", marginLeft: "0.75rem" }}>connecting…</span>
          )}
        </div>

        <div style={{
          position: "relative",
          background: "#0a0a0a",
          borderRadius: "6px",
          overflow: "hidden",
          aspectRatio: "16/9",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}>
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", objectFit: "contain" }}
          />
          {playerState !== "playing" && (
            <div style={{
              position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
              background: "#0a0a0a",
              display: "flex", alignItems: "center", justifyContent: "center",
              color: "#555", fontSize: "0.9rem", textAlign: "center", padding: "2rem",
            }}>
              {!running
                ? "Start the pipeline to view the stream"
                : playerState === "connecting"
                ? "Connecting to MediaMTX…"
                : playerState === "error"
                ? `Player error: ${playerError}`
                : "Waiting for stream…"}
            </div>
          )}
        </div>

        <p style={{ color: "#444", fontSize: "0.72rem", marginTop: "0.4rem" }}>
          Receiving via WHEP · {mediamtxUrl ?? "…"}/mxl2webrtc/whep
        </p>
      </div>

      {/* Error banner */}
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
