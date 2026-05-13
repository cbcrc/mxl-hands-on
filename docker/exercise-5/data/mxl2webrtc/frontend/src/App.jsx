// SPDX-FileCopyrightText: 2025 Contributors to the Media eXchange Layer project.
// SPDX-License-Identifier: Apache-2.0
import React, { useState, useEffect, useCallback, useRef } from "react";

const API = `http://${window.location.hostname}:9650`;
const WS_URL = `ws://${window.location.hostname}:8443`;

// ── Styles ────────────────────────────────────────────────────────────────────

const sectionStyle = {
  background: "#1c1c1c",
  borderRadius: "8px",
  padding: "1.5rem",
  marginBottom: "1rem",
};

const labelStyle = {
  display: "block",
  marginBottom: "0.6rem",
  color: "#aaa",
  fontSize: "0.85rem",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};

const stateBadge = (state) => ({
  display: "inline-block",
  padding: "0.25rem 0.75rem",
  borderRadius: "20px",
  background: state === "playing" ? "#1a5c2a" : state === "error" ? "#5c1a1a" : "#3a3a3a",
  color: state === "playing" ? "#4caf50" : state === "error" ? "#f44336" : "#888",
  fontSize: "0.8rem",
  fontWeight: 600,
  marginLeft: "1rem",
});

const connDot = (connected) => ({
  display: "inline-block",
  width: "10px",
  height: "10px",
  borderRadius: "50%",
  background: connected ? "#4caf50" : "#444",
  marginRight: "8px",
  flexShrink: 0,
});

// ── WebRTC player hook ────────────────────────────────────────────────────────

/**
 * Connects to the webrtcsink embedded signaling server and returns a ref
 * to attach to a <video> element.
 *
 * Protocol (gst-plugins-rs webrtcsink built-in signaler):
 * 1. Connect to ws://<hostname>:8443
 * 2. Receive: {"type": "welcome", "peer_id": "<server_id>"}
 * 3. Send:    {"type": "set-peer-id", "peer_id": "<random_client_id>"}
 * 4. Send:    {"type": "start-session", "peer_id": "<server_id>"}
 * 5. Receive: {"type": "session-started", ...}
 * 6. Receive: {"type": "peer", "sdp": {"type": "offer", "sdp": "..."}}
 * 7. setRemoteDescription(offer), createAnswer, setLocalDescription(answer)
 * 8. Send:    {"type": "peer", "sdp": answer, "sessionId": ...}
 * 9. ICE exchange via {"type": "ice", ...} messages
 * 10. On track: attach to video element
 */
function useWebRtcPlayer(pipelinePlaying) {
  const videoRef = useRef(null);
  const [playerState, setPlayerState] = useState("idle"); // idle / connecting / playing / error
  const [playerError, setPlayerError] = useState(null);
  const wsRef  = useRef(null);
  const pcRef  = useRef(null);
  const sessionIdRef = useRef(null);

  const cleanup = useCallback(() => {
    if (pcRef.current) {
      pcRef.current.close();
      pcRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    sessionIdRef.current = null;
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }, []);

  const connect = useCallback(() => {
    cleanup();
    setPlayerState("connecting");
    setPlayerError(null);

    const clientId = Math.random().toString(36).slice(2, 10);
    let serverId = null;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onmessage = async (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }

      if (msg.type === "welcome") {
        serverId = msg.peer_id;
        ws.send(JSON.stringify({ type: "set-peer-id", peer_id: clientId }));
        ws.send(JSON.stringify({ type: "start-session", peer_id: serverId }));
        return;
      }

      if (msg.type === "session-started") {
        sessionIdRef.current = msg.session_id;
        return;
      }

      if (msg.type === "peer" && msg.sdp?.type === "offer") {
        const sessionId = msg.sessionId ?? sessionIdRef.current;
        sessionIdRef.current = sessionId;

        const pc = new RTCPeerConnection({
          iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
        });
        pcRef.current = pc;

        pc.ontrack = (e) => {
          if (videoRef.current && e.streams[0]) {
            videoRef.current.srcObject = e.streams[0];
            setPlayerState("playing");
          }
        };

        pc.onicecandidate = (e) => {
          if (e.candidate && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
              type: "ice",
              sessionId,
              candidate: e.candidate.toJSON(),
            }));
          }
        };

        pc.onconnectionstatechange = () => {
          if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
            setPlayerState("error");
            setPlayerError("WebRTC connection " + pc.connectionState);
          }
        };

        await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);

        ws.send(JSON.stringify({ type: "peer", sessionId, sdp: answer }));
        return;
      }

      if (msg.type === "ice" && pcRef.current) {
        try {
          await pcRef.current.addIceCandidate(new RTCIceCandidate(msg.candidate));
        } catch (e) {
          console.warn("addIceCandidate error:", e);
        }
        return;
      }
    };

    ws.onerror = () => {
      setPlayerState("error");
      setPlayerError("WebSocket connection failed");
    };

    ws.onclose = () => {
      if (playerState !== "idle") {
        setPlayerState("idle");
      }
    };
  }, [cleanup]);

  useEffect(() => {
    if (pipelinePlaying) {
      // Small delay to let webrtcsink start its signaling server
      const t = setTimeout(connect, 1000);
      return () => {
        clearTimeout(t);
        cleanup();
        setPlayerState("idle");
      };
    } else {
      cleanup();
      setPlayerState("idle");
    }
  }, [pipelinePlaying, connect, cleanup]);

  return { videoRef, playerState, playerError };
}

// ── App component ─────────────────────────────────────────────────────────────

export default function App() {
  const [status, setStatus] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/status`);
      const d = await r.json();
      setStatus(d);
    } catch {}
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 2000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  const pipelinePlaying = status?.state === "playing";
  const { videoRef, playerState, playerError } = useWebRtcPlayer(pipelinePlaying);

  const videoConnected = !!status?.video_flow_id;
  const audioConnected = !!status?.audio_flow_id;
  const pipelineState  = status?.state ?? "idle";

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ fontSize: "1.6rem", fontWeight: 700 }}>
          MXL → WebRTC Gateway
          <span style={stateBadge(pipelineState)}>
            {pipelineState === "playing" ? "● PLAYING" : pipelineState === "error" ? "✕ ERROR" : "○ IDLE"}
          </span>
        </h1>
      </div>

      {/* MXL connection status */}
      <div style={sectionStyle}>
        <span style={labelStyle}>MXL Connections</span>
        <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
          <div style={{ display: "flex", alignItems: "center", fontSize: "0.85rem" }}>
            <span style={connDot(videoConnected)} />
            <span style={{ color: "#aaa", width: "60px" }}>Video</span>
            <span style={{ fontFamily: "monospace", color: videoConnected ? "#ccc" : "#444" }}>
              {status?.video_flow_id || "—"}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", fontSize: "0.85rem" }}>
            <span style={connDot(audioConnected)} />
            <span style={{ color: "#aaa", width: "60px" }}>Audio</span>
            <span style={{ fontFamily: "monospace", color: audioConnected ? "#ccc" : "#444" }}>
              {status?.audio_flow_id || "—"}
            </span>
          </div>
        </div>
        {!videoConnected && !audioConnected && (
          <p style={{ color: "#555", fontSize: "0.75rem", marginTop: "0.75rem" }}>
            Waiting for NMOS IS-05 activation on video and audio receivers…
          </p>
        )}
      </div>

      {/* WebRTC player */}
      <div style={sectionStyle}>
        <span style={labelStyle}>WebRTC Player</span>
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
            muted={false}
            style={{
              width: "100%",
              height: "100%",
              display: playerState === "playing" ? "block" : "none",
              objectFit: "contain",
            }}
          />
          {playerState !== "playing" && (
            <div style={{ color: "#555", fontSize: "0.9rem", textAlign: "center", padding: "2rem" }}>
              {!pipelinePlaying
                ? "Waiting for stream…"
                : playerState === "connecting"
                ? "Connecting to WebRTC…"
                : playerState === "error"
                ? `Error: ${playerError}`
                : "Waiting for stream…"}
            </div>
          )}
        </div>
        <p style={{ color: "#555", fontSize: "0.72rem", marginTop: "0.5rem" }}>
          Signaling: ws://{window.location.hostname}:8443 · ICE ports: 50000–50020/UDP
        </p>
      </div>

      {/* Error banner */}
      {status?.error && (
        <div style={{
          ...sectionStyle,
          background: "#2a0a0a",
          border: "1px solid #5c1a1a",
        }}>
          <span style={{ color: "#f44336", fontSize: "0.85rem" }}>
            Pipeline error: {status.error}
          </span>
        </div>
      )}
    </div>
  );
}
