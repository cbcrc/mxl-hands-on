import React, { useState, useEffect, useCallback } from "react";

// API lives on port 9610 of the same host
const API = `http://${window.location.hostname}:9610`;

const sectionStyle = {
  background: "#1c1c1c",
  borderRadius: "8px",
  padding: "1.5rem",
  marginBottom: "1rem",
};
const labelStyle = { display: "block", marginBottom: "0.4rem", color: "#aaa", fontSize: "0.85rem" };
const selectStyle = {
  width: "100%",
  padding: "0.5rem",
  background: "#2a2a2a",
  color: "#fff",
  border: "1px solid #444",
  borderRadius: "4px",
  fontSize: "1rem",
};
const inputStyle = { ...selectStyle };
const checkboxRow = { display: "flex", alignItems: "center", gap: "0.75rem" };
const statusBadge = (playing) => ({
  display: "inline-block",
  padding: "0.25rem 0.75rem",
  borderRadius: "20px",
  background: playing ? "#1a5c2a" : "#5c1a1a",
  color: playing ? "#4caf50" : "#f44336",
  fontSize: "0.8rem",
  fontWeight: 600,
  marginLeft: "1rem",
});

export default function App() {
  const [status, setStatus] = useState(null);
  const [patterns, setPatterns] = useState({ video: [], audio: [] });
  const [identDraft, setIdentDraft] = useState("");

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/status`);
      const d = await r.json();
      setStatus(d);
    } catch {}
  }, []);

  const fetchPatterns = useCallback(async () => {
    try {
      const r = await fetch(`${API}/patterns`);
      const d = await r.json();
      setPatterns(d);
    } catch {}
  }, []);

  useEffect(() => {
    fetchPatterns();
    fetchStatus();
    const id = setInterval(fetchStatus, 2000);
    return () => clearInterval(id);
  }, [fetchStatus, fetchPatterns]);

  // Sync ident draft with server value once loaded
  useEffect(() => {
    if (status && identDraft === "" && status.ident !== "") {
      setIdentDraft(status.ident);
    }
  }, [status]);

  const post = async (path, body) => {
    await fetch(`${API}${path}`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
    });
    fetchStatus();
  };

  const playing = status?.state === "playing";

  return (
    <div style={{ maxWidth: "640px", width: "100%" }}>
      {/* Header */}
      <div style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ fontSize: "1.6rem", fontWeight: 700 }}>
          MXL Test Generator
          <span style={statusBadge(playing)}>
            {playing ? "● ACTIVE" : "○ IDLE"}
          </span>
        </h1>
        {status && (
          <p style={{ color: "#666", fontSize: "0.8rem", marginTop: "0.4rem" }}>
            Video: {status.video_flow_id || "—"} &nbsp;|&nbsp; Audio: {status.audio_flow_id || "—"}
          </p>
        )}
      </div>

      {/* Video Pattern */}
      <div style={sectionStyle}>
        <label style={labelStyle}>Video Test Pattern</label>
        <select
          style={selectStyle}
          value={status?.video_pattern ?? ""}
          onChange={(e) => post("/video-test-pattern", { pattern: e.target.value })}
        >
          {patterns.video.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>

      {/* Audio Pattern */}
      <div style={sectionStyle}>
        <label style={labelStyle}>Audio Test Pattern</label>
        <select
          style={selectStyle}
          value={status?.audio_pattern ?? ""}
          onChange={(e) => post("/audio-test-pattern", { pattern: e.target.value })}
        >
          {patterns.audio.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>

      {/* Timecode */}
      <div style={sectionStyle}>
        <div style={checkboxRow}>
          <input
            type="checkbox"
            id="timecode"
            style={{ width: "18px", height: "18px", cursor: "pointer" }}
            checked={status?.timecode ?? true}
            onChange={(e) => post("/timecode", { enabled: e.target.checked })}
          />
          <label htmlFor="timecode" style={{ cursor: "pointer", fontSize: "1rem" }}>
            Burn-in Timecode
          </label>
        </div>
      </div>

      {/* Ident */}
      <div style={sectionStyle}>
        <label style={labelStyle}>Ident Text (overlay)</label>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <input
            style={{ ...inputStyle, flex: 1 }}
            type="text"
            placeholder="e.g. Camera 1"
            value={identDraft}
            onChange={(e) => setIdentDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && post("/ident", { text: identDraft })}
          />
          <button
            style={{
              padding: "0.5rem 1rem",
              background: "#0d7c3e",
              color: "#fff",
              border: "none",
              borderRadius: "4px",
              cursor: "pointer",
              fontWeight: 600,
            }}
            onClick={() => post("/ident", { text: identDraft })}
          >
            Apply
          </button>
        </div>
        <p style={{ color: "#666", fontSize: "0.75rem", marginTop: "0.3rem" }}>
          Press Enter or click Apply to update
        </p>
      </div>
    </div>
  );
}
