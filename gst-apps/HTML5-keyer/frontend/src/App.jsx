import React, { useState, useEffect, useCallback } from "react";

const API = `http://${window.location.hostname}:9640`;

// ── Styles ───────────────────────────────────────────────────────────────────

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

const statusBadge = (active) => ({
  display: "inline-block",
  padding: "0.25rem 0.75rem",
  borderRadius: "20px",
  background: active ? "#1a5c2a" : "#3a3a3a",
  color: active ? "#4caf50" : "#888",
  fontSize: "0.8rem",
  fontWeight: 600,
  marginLeft: "1rem",
});

const keyBtnStyle = (keyOn) => ({
  width: "100%",
  padding: "2rem 1rem",
  borderRadius: "8px",
  cursor: "pointer",
  fontWeight: 700,
  fontSize: "1.6rem",
  letterSpacing: "0.1em",
  transition: "all 0.15s",
  border: keyOn ? "3px solid #4caf50" : "3px solid #555",
  background: keyOn ? "#0d4a1f" : "#1a1a1a",
  color: keyOn ? "#4caf50" : "#888",
});

const infoRowStyle = {
  display: "flex",
  justifyContent: "space-between",
  padding: "0.35rem 0",
  fontSize: "0.8rem",
  borderBottom: "1px solid #2a2a2a",
};

const infoLabelStyle = { color: "#aaa" };
const infoValueStyle = (active) => ({
  color: active ? "#ccc" : "#444",
  fontFamily: "monospace",
});

// ── Component ─────────────────────────────────────────────────────────────────

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
    const id = setInterval(fetchStatus, 1000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  const toggleKey = async () => {
    const newState = !(status?.key_enabled ?? false);
    try {
      await fetch(`${API}/keyer-control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: newState }),
      });
      fetchStatus();
    } catch {}
  };

  const keyOn      = status?.key_enabled ?? false;
  const inputConn  = status?.input_connected ?? false;
  const pipelineOk = status?.pipeline_running ?? false;

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ fontSize: "1.6rem", fontWeight: 700 }}>
          MXL HTML5 Keyer
          <span style={statusBadge(keyOn)}>
            {keyOn ? "● KEY ON" : "○ KEY OFF"}
          </span>
        </h1>
        {status && (
          <p style={{ color: "#555", fontSize: "0.75rem", marginTop: "0.4rem" }}>
            Output flow: {status.output_flow_id || "—"}
          </p>
        )}
      </div>

      {/* Key toggle button */}
      <div style={sectionStyle}>
        <span style={labelStyle}>Key Control</span>
        <button
          style={keyBtnStyle(keyOn)}
          onClick={toggleKey}
          disabled={!pipelineOk}
          title={pipelineOk ? "Click to toggle key" : "Pipeline not ready"}
        >
          {keyOn ? "● KEY ON" : "○ KEY OFF"}
          {keyOn && (
            <div style={{ fontSize: "0.9rem", marginTop: "0.4rem", fontWeight: 400 }}>
              HTML5 graphics overlay active
            </div>
          )}
        </button>
        <p style={{ color: "#555", fontSize: "0.72rem", marginTop: "0.8rem" }}>
          Toggle to enable or disable the HTML5 graphics overlay. The key uses
          alpha compositing via glvideomixer — no pipeline interruption.
        </p>
      </div>

      {/* Status panel */}
      <div style={sectionStyle}>
        <span style={labelStyle}>Status</span>
        <div style={infoRowStyle}>
          <span style={infoLabelStyle}>Pipeline</span>
          <span style={infoValueStyle(pipelineOk)}>
            {pipelineOk ? "● Running" : "○ Stopped"}
          </span>
        </div>
        <div style={infoRowStyle}>
          <span style={infoLabelStyle}>MXL Input</span>
          <span style={infoValueStyle(inputConn)}>
            {inputConn ? "● Connected" : "○ No flow"}
          </span>
        </div>
        <div style={{ ...infoRowStyle, borderBottom: "none" }}>
          <span style={infoLabelStyle}>Input flow ID</span>
          <span style={infoValueStyle(inputConn)}>
            {status?.input_flow_id || "—"}
          </span>
        </div>
      </div>
    </div>
  );
}
