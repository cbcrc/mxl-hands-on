// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import React, { useState, useEffect, useCallback } from "react";

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
  input: {
    width: "100%",
    padding: "0.45rem 0.6rem",
    background: "#2a2a2a",
    color: "#fff",
    border: "1px solid #444",
    borderRadius: "4px",
    fontSize: "0.95rem",
    boxSizing: "border-box",
  },
  inputDisabled: {
    opacity: 0.4,
    cursor: "not-allowed",
  },
  row: { display: "flex", gap: "0.75rem", alignItems: "flex-start" },
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

const badge = (running, stabilising) => ({
  display: "inline-block",
  padding: "0.2rem 0.65rem",
  borderRadius: "20px",
  background: !running ? "#3a3a3a" : stabilising ? "#3a2a00" : "#1a5c2a",
  color:      !running ? "#888"    : stabilising ? "#f0a000" : "#4caf50",
  fontSize: "0.75rem",
  fontWeight: 700,
  marginLeft: "0.75rem",
  verticalAlign: "middle",
});

// ── Helpers ───────────────────────────────────────────────────────────────────

async function post(path, body) {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.detail || r.statusText);
  }
  return r.json();
}

function Input({ label, value, onChange, disabled, placeholder }) {
  return (
    <div>
      {label && <label style={S.label}>{label}</label>}
      <input
        type="text"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        style={{ ...S.input, ...(disabled ? S.inputDisabled : {}) }}
      />
    </div>
  );
}

function Select({ label, value, onChange, options, disabled }) {
  return (
    <div>
      {label && <label style={S.label}>{label}</label>}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        style={{ ...S.input, ...(disabled ? S.inputDisabled : {}) }}
      >
        {options.map((o) =>
          typeof o === "string" ? (
            <option key={o} value={o}>{o}</option>
          ) : (
            <option key={o.value} value={o.value}>{o.label}</option>
          )
        )}
      </select>
    </div>
  );
}

// ── Default flow config ───────────────────────────────────────────────────────

const DEFAULT_FLOWS = {
  video: { description: "hls-video-out", label: "hls-video" },
  audio: { description: "hls-audio-out", label: "hls-audio" },
};

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [status,    setStatus]    = useState(null);
  const [domains,   setDomains]   = useState([]);
  const [domain,    setDomain]    = useState("");
  const [grouphint, setGrouphint] = useState("HLS2MXL");
  const [hlsUrl,    setHlsUrl]    = useState("");
  const [flows,     setFlows]     = useState(DEFAULT_FLOWS);
  const [opUrl,     setOpUrl]     = useState("");
  const [error,     setError]     = useState("");

  const running     = status?.running     ?? false;
  const stabilising = status?.stabilising ?? false;

  // ── Data fetching ─────────────────────────────────────────────────────────

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/pipeline/status`);
      setStatus(await r.json());
    } catch {}
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const d = await fetch(`${API}/domains`).then((r) => r.json());
        setDomains(d.domains ?? []);
        if (d.domains?.length > 0) setDomain(d.domains[0].path);
      } catch {}
    })();
    fetchStatus();
    const id = setInterval(fetchStatus, 2000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  // Sync the operation URL field when the pipeline starts
  useEffect(() => {
    if (running && status?.hls_url) setOpUrl(status.hls_url);
  }, [running]);

  // ── Validation ────────────────────────────────────────────────────────────

  const canStart =
    !running &&
    domain !== "" &&
    hlsUrl.trim() !== "" &&
    flows.video.description.trim() !== "" && flows.video.label.trim() !== "" &&
    flows.audio.description.trim() !== "" && flows.audio.label.trim() !== "";

  // ── Handlers ──────────────────────────────────────────────────────────────

  const updateFlow = (key, field, value) =>
    setFlows((prev) => ({ ...prev, [key]: { ...prev[key], [field]: value } }));

  const handleStart = async () => {
    setError("");
    try {
      await post("/pipeline/start", {
        domain,
        grouphint,
        hls_url: hlsUrl.trim(),
        video: flows.video,
        audio: flows.audio,
      });
      await fetchStatus();
    } catch (e) {
      setError(e.message);
    }
  };

  const handleStop = async () => {
    setError("");
    try {
      await post("/pipeline/stop", {});
      await fetchStatus();
    } catch (e) {
      setError(e.message);
    }
  };

  const handleApply = async () => {
    if (!opUrl.trim() || stabilising) return;
    setError("");
    try {
      await post("/hls/apply", { url: opUrl.trim() });
      await fetchStatus();
    } catch (e) {
      setError(e.message);
    }
  };

  const domainOptions = domains.map((d) => ({
    value: d.path,
    label: d.label || d.path,
  }));

  // ── Flow table row ────────────────────────────────────────────────────────

  const FlowRow = ({ flowKey, flowLabel }) => {
    const f = flows[flowKey];
    return (
      <tr>
        <td style={{ padding: "0.4rem 0.5rem", color: "#ccc", whiteSpace: "nowrap" }}>
          {flowLabel}
        </td>
        <td style={{ padding: "0.4rem 0.5rem" }}>
          <input
            style={{ ...S.input, ...(running ? S.inputDisabled : {}) }}
            value={f.description}
            onChange={(e) => updateFlow(flowKey, "description", e.target.value)}
            disabled={running}
            placeholder="description"
          />
        </td>
        <td style={{ padding: "0.4rem 0.5rem" }}>
          <input
            style={{ ...S.input, ...(running ? S.inputDisabled : {}) }}
            value={f.label}
            onChange={(e) => updateFlow(flowKey, "label", e.target.value)}
            disabled={running}
            placeholder="label"
          />
        </td>
      </tr>
    );
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{ maxWidth: "800px", width: "100%", margin: "0 auto" }}>

      {/* Header */}
      <div style={{ marginBottom: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <img src="/cbc-logo.png" alt="CBC Radio-Canada" style={{ height: "2.2rem", objectFit: "contain" }} />
          <h1 style={{ fontSize: "1.5rem", fontWeight: 700, margin: 0 }}>
            MXL HLS Gateway
            <span style={badge(running, stabilising)}>
              {!running ? "○ STOPPED" : stabilising ? "◌ STABILISING" : "● RUNNING"}
            </span>
          </h1>
        </div>
        {running && status?.flow_uuids && (
          <p style={{ color: "#555", fontSize: "0.75rem", marginTop: "0.4rem", fontFamily: "monospace" }}>
            {Object.entries(status.flow_uuids).map(([k, v]) => `${k}: ${v}`).join(" · ")}
          </p>
        )}
      </div>

      {error && (
        <div style={{ background: "#3a1010", border: "1px solid #8b1a1a", borderRadius: "6px", padding: "0.6rem 1rem", marginBottom: "1rem", color: "#f88" }}>
          {error}
        </div>
      )}

      {/* ── SETUP SECTION ── */}
      <div style={{ ...S.card, border: "1px solid #2a2a2a" }}>
        <div style={S.sectionTitle}>Setup</div>
        <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>

          {/* Domain + Group Hint */}
          <div style={S.row}>
            <div style={{ flex: 2 }}>
              <Select
                label="MXL Domain"
                value={domain}
                onChange={setDomain}
                options={domainOptions.length > 0 ? domainOptions : [{ value: "", label: "No domains found" }]}
                disabled={running}
              />
            </div>
            <div style={{ flex: 1 }}>
              <Input
                label="Group Hint"
                value={grouphint}
                onChange={setGrouphint}
                disabled={running}
              />
            </div>
          </div>

          {/* HLS URL */}
          <Input
            label="HLS Stream URL"
            value={hlsUrl}
            onChange={setHlsUrl}
            disabled={running}
            placeholder="https://example.com/stream/playlist.m3u8"
          />

          {/* Flow configuration table */}
          <div>
            <label style={S.label}>Flow Configuration</label>
            <table style={{ width: "100%", borderCollapse: "collapse", background: "#222", borderRadius: "6px", overflow: "hidden" }}>
              <thead>
                <tr style={{ background: "#2a2a2a" }}>
                  <th style={{ padding: "0.4rem 0.5rem", textAlign: "left", color: "#888", fontSize: "0.78rem", fontWeight: 600 }}>Flow</th>
                  <th style={{ padding: "0.4rem 0.5rem", textAlign: "left", color: "#888", fontSize: "0.78rem", fontWeight: 600 }}>Description</th>
                  <th style={{ padding: "0.4rem 0.5rem", textAlign: "left", color: "#888", fontSize: "0.78rem", fontWeight: 600 }}>Label</th>
                </tr>
              </thead>
              <tbody>
                <FlowRow flowKey="video" flowLabel="Video" />
                <FlowRow flowKey="audio" flowLabel="Audio" />
              </tbody>
            </table>
          </div>

          {/* Start / Stop */}
          <div>
            {running ? (
              <button style={btn("danger")} onClick={handleStop}>
                ■ Stop Pipeline
              </button>
            ) : (
              <button style={btn("success", !canStart)} onClick={handleStart} disabled={!canStart}>
                ▶ Start Pipeline
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── OPERATION SECTION ── */}
      <div style={{ opacity: running ? 1 : 0.35, pointerEvents: running ? "auto" : "none", transition: "opacity 0.2s" }}>
        <div style={{ ...S.card, border: "1px solid #2a2a2a" }}>
          <div style={S.sectionTitle}>Operation</div>

          {stabilising && (
            <div style={{ background: "#3a2a00", border: "1px solid #f0a000", borderRadius: "6px", padding: "0.5rem 0.75rem", marginBottom: "0.75rem", color: "#f0a000", fontSize: "0.85rem" }}>
              ◌ Stabilising… waiting for HLS stream to settle (10 s buffer)
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <label style={S.label}>HLS Stream URL</label>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <input
                style={{ ...S.input, flex: 1, ...(stabilising ? S.inputDisabled : {}) }}
                type="text"
                placeholder="https://example.com/stream/playlist.m3u8"
                value={opUrl}
                onChange={(e) => setOpUrl(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !stabilising && handleApply()}
                disabled={stabilising}
              />
              <button
                style={btn("primary", stabilising || !opUrl.trim())}
                onClick={handleApply}
                disabled={stabilising || !opUrl.trim()}
              >
                Apply
              </button>
            </div>
            <p style={{ color: "#555", fontSize: "0.75rem" }}>
              Applying a new URL rebuilds the pipeline with a fresh 10 s stabilisation window.
            </p>
          </div>
        </div>
      </div>

    </div>
  );
}
