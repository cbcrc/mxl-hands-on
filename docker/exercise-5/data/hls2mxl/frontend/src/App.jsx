import React, { useState, useEffect, useCallback } from "react";

const API = `http://${window.location.hostname}:9620`;

const STATE_COLOR = {
  playing: "#4caf50",
  idle:    "#64748b",
  error:   "#f44336",
};

const sectionStyle = {
  background: "#1c1c1c",
  borderRadius: "8px",
  padding: "1.5rem",
  marginBottom: "1rem",
};
const labelStyle = {
  display: "block",
  marginBottom: "0.4rem",
  color: "#aaa",
  fontSize: "0.85rem",
};
const inputStyle = {
  width: "100%",
  padding: "0.5rem",
  background: "#2a2a2a",
  color: "#fff",
  border: "1px solid #444",
  borderRadius: "4px",
  fontSize: "1rem",
};
const btnStyle = (color = "#0d7c3e") => ({
  padding: "0.55rem 1.2rem",
  background: color,
  color: "#fff",
  border: "none",
  borderRadius: "4px",
  cursor: "pointer",
  fontWeight: 600,
  fontSize: "0.95rem",
});
const statusBadge = (state) => ({
  display: "inline-block",
  padding: "0.25rem 0.75rem",
  borderRadius: "20px",
  background: state === "playing" ? "#1a5c2a" : state === "error" ? "#5c1a1a" : "#2a2a2a",
  color: STATE_COLOR[state] || "#aaa",
  fontSize: "0.8rem",
  fontWeight: 600,
  marginLeft: "1rem",
  textTransform: "uppercase",
});

export default function App() {
  const [status, setStatus] = useState(null);
  const [urlDraft, setUrlDraft] = useState("");

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/status`);
      setStatus(await r.json());
    } catch {}
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 2000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  // Pre-fill draft from server once
  useEffect(() => {
    if (status && !urlDraft && status.hls_url) {
      setUrlDraft(status.hls_url);
    }
  }, [status?.state]);

  const post = async (path, body = undefined) => {
    await fetch(`${API}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    fetchStatus();
  };

  const handleApply = async () => {
    if (!urlDraft.trim()) return;
    await post("/hls-link", { url: urlDraft.trim() });
    await post("/apply");
  };

  const state = status?.state ?? "idle";

  return (
    <div style={{ maxWidth: "640px", width: "100%" }}>
      {/* Header */}
      <div style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ fontSize: "1.6rem", fontWeight: 700 }}>
          HLS → MXL Gateway
          <span style={statusBadge(state)}>● {state}</span>
        </h1>
        {status && (
          <p style={{ color: "#666", fontSize: "0.8rem", marginTop: "0.4rem" }}>
            Video: {status.video_flow_id || "—"} &nbsp;|&nbsp; Audio: {status.audio_flow_id || "—"}
          </p>
        )}
      </div>

      {/* Error banner */}
      {status?.error && (
        <div style={{
          background: "#3b1a1a", border: "1px solid #f44", borderRadius: "6px",
          padding: "0.75rem 1rem", marginBottom: "1rem", color: "#f44", fontSize: "0.9rem",
        }}>
          ⚠ {status.error}
        </div>
      )}

      {/* HLS URL input */}
      <div style={sectionStyle}>
        <label style={labelStyle}>HLS Stream URL (.m3u8)</label>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <input
            style={{ ...inputStyle, flex: 1 }}
            type="url"
            placeholder="https://example.com/stream/playlist.m3u8"
            value={urlDraft}
            onChange={(e) => setUrlDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleApply()}
          />
          <button style={btnStyle()} onClick={handleApply}>
            Apply
          </button>
        </div>
        <p style={{ color: "#555", fontSize: "0.75rem", marginTop: "0.3rem" }}>
          Press Enter or click Apply to connect
        </p>
      </div>

      {/* Stop button */}
      {state === "playing" && (
        <div style={sectionStyle}>
          <button
            style={{ ...btnStyle("#7c1a1a"), width: "100%" }}
            onClick={() => post("/stop")}
          >
            ■ Stop Stream
          </button>
        </div>
      )}

      {/* Flow info */}
      {status?.hls_url && (
        <div style={{ ...sectionStyle, fontSize: "0.85rem", color: "#666" }}>
          <strong style={{ color: "#aaa" }}>Current URL:</strong>
          <div style={{ marginTop: "0.3rem", wordBreak: "break-all" }}>
            {status.hls_url}
          </div>
        </div>
      )}
    </div>
  );
}
