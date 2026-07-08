// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import React, { useCallback, useEffect, useState } from "react";

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
  inputDisabled: { opacity: 0.4, cursor: "not-allowed" },
  row: { display: "flex", gap: "0.75rem", alignItems: "flex-start" },
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
  transition: "opacity 0.15s",
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

// ── Stream info rendering ─────────────────────────────────────────────────────

function fmtFramerate(fr) {
  if (!fr || typeof fr !== "string" || !fr.includes("/")) return fr ?? "?";
  const [n, d] = fr.split("/").map(Number);
  if (!d) return `${n}`;
  const fps = n / d;
  return Number.isInteger(fps) ? `${fps}` : fps.toFixed(2);
}

function StreamLine({ s }) {
  if (s.type === "video") {
    return (
      <div style={{ fontSize: "0.85rem", color: "#ccc", marginBottom: "0.2rem" }}>
        <strong style={{ color: "#fff" }}>Video:</strong>{" "}
        {s.codec} · {s.width}×{s.height} @ {fmtFramerate(s.framerate)} fps
      </div>
    );
  }
  return (
    <div style={{ fontSize: "0.85rem", color: "#ccc", marginBottom: "0.2rem" }}>
      <strong style={{ color: "#fff" }}>Audio:</strong>{" "}
      {s.codec} · {s.sample_rate} Hz · {s.channels} ch
    </div>
  );
}

// ── Defaults ──────────────────────────────────────────────────────────────────

const DEFAULT_FLOWS = {
  video: { active: true, description: "video-out-1", label: "clip-player-video" },
  audio: { active: true, description: "audio-out-1", label: "clip-player-audio" },
};

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  // Fetched data
  const [status,  setStatus]  = useState(null);
  const [domains, setDomains] = useState([]);
  const [files,   setFiles]   = useState([]);

  // Setup form state
  const [domain,    setDomain]    = useState("");
  const [file,      setFile]      = useState("");
  const [grouphint, setGrouphint] = useState("Clip-Player");
  const [flows,     setFlows]     = useState(DEFAULT_FLOWS);
  const [probe,     setProbe]     = useState(null);   // probe result for selected file
  const [probing,   setProbing]   = useState(false);

  const [error, setError] = useState("");

  const running = status?.running ?? false;

  // ── Data fetching ─────────────────────────────────────────────────────────

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/pipeline/status`);
      setStatus(await r.json());
    } catch {}
  }, []);

  const fetchFiles = useCallback(async () => {
    try {
      const r = await fetch(`${API}/files`);
      const d = await r.json();
      setFiles(d.files ?? []);
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
    fetchFiles();
    fetchStatus();
    const id = setInterval(fetchStatus, 2000);
    return () => clearInterval(id);
  }, [fetchFiles, fetchStatus]);

  // Auto-probe when file selection changes
  useEffect(() => {
    if (!file) { setProbe(null); return; }
    let aborted = false;
    setProbing(true);
    setError("");
    (async () => {
      try {
        const r = await fetch(`${API}/files/probe?path=${encodeURIComponent(file)}`);
        const d = await r.json();
        if (aborted) return;
        if (!r.ok) {
          setError(d.detail || "probe failed");
          setProbe(null);
        } else {
          setProbe(d);
          // Disable flows the file does not contain
          setFlows((prev) => ({
            video: { ...prev.video, active: prev.video.active && d.has_video },
            audio: { ...prev.audio, active: prev.audio.active && d.has_audio },
          }));
        }
      } catch (e) {
        if (!aborted) { setError(String(e)); setProbe(null); }
      } finally {
        if (!aborted) setProbing(false);
      }
    })();
    return () => { aborted = true; };
  }, [file]);

  // ── Setup validation ──────────────────────────────────────────────────────

  const activeFlowsValid = ["video", "audio"].every((k) => {
    const f = flows[k];
    if (!f.active) return true;
    return f.description.trim() !== "" && f.label.trim() !== "";
  });

  const anyActive =
    (flows.video.active && probe?.has_video) ||
    (flows.audio.active && probe?.has_audio);

  const canStart = !running &&
    domain !== "" &&
    file !== "" &&
    probe !== null &&
    !probing &&
    anyActive &&
    activeFlowsValid;

  // ── Handlers ──────────────────────────────────────────────────────────────

  const updateFlow = (key, field, value) =>
    setFlows((prev) => ({ ...prev, [key]: { ...prev[key], [field]: value } }));

  const handleStart = async () => {
    setError("");
    try {
      await post("/pipeline/start", {
        domain,
        file,
        grouphint,
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

  // ── Domain / file options ────────────────────────────────────────────────

  const domainOptions = domains.map((d) => ({
    value: d.path,
    label: d.label || d.path,
  }));

  const fileOptions = files.length > 0
    ? [{ value: "", label: "— select a file —" }, ...files.map((f) => ({ value: f, label: f }))]
    : [{ value: "", label: "No files found in /home/file" }];

  // ── Flow row ──────────────────────────────────────────────────────────────

  const FlowRow = ({ flowKey, label, present }) => {
    const f = flows[flowKey];
    const rowDisabled = running || !present;
    return (
      <tr style={{ opacity: present ? 1 : 0.4 }}>
        <td style={{ padding: "0.4rem 0.5rem", color: "#ccc", whiteSpace: "nowrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: rowDisabled ? "not-allowed" : "pointer" }}>
            <input
              type="checkbox"
              checked={f.active && present}
              onChange={(e) => !rowDisabled && updateFlow(flowKey, "active", e.target.checked)}
              disabled={rowDisabled}
              style={{ width: "16px", height: "16px" }}
            />
            {label}
          </label>
        </td>
        <td style={{ padding: "0.4rem 0.5rem" }}>
          <input
            style={{ ...S.input, ...(rowDisabled ? S.inputDisabled : {}) }}
            value={f.description}
            onChange={(e) => updateFlow(flowKey, "description", e.target.value)}
            disabled={rowDisabled}
            placeholder="description"
          />
        </td>
        <td style={{ padding: "0.4rem 0.5rem" }}>
          <input
            style={{ ...S.input, ...(rowDisabled ? S.inputDisabled : {}) }}
            value={f.label}
            onChange={(e) => updateFlow(flowKey, "label", e.target.value)}
            disabled={rowDisabled}
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
            MXL File Player
            <span style={badge(running)}>{running ? "● RUNNING" : "○ STOPPED"}</span>
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
          {/* Domain + grouphint */}
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

          {/* File + refresh */}
          <div>
            <label style={S.label}>Media File</label>
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "stretch" }}>
              <div style={{ flex: 1 }}>
                <Select
                  value={file}
                  onChange={setFile}
                  options={fileOptions}
                  disabled={running}
                />
              </div>
              <button
                style={btn("primary", running)}
                onClick={fetchFiles}
                disabled={running}
                title="Re-scan /home/file"
              >
                ↻ Refresh
              </button>
            </div>
            {probing && (
              <p style={{ color: "#888", fontSize: "0.8rem", marginTop: "0.4rem" }}>
                Probing file…
              </p>
            )}
          </div>

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
                <FlowRow flowKey="video" label="Video" present={!!probe?.has_video} />
                <FlowRow flowKey="audio" label="Audio" present={!!probe?.has_audio} />
              </tbody>
            </table>
            {probe && !probe.has_video && !probe.has_audio && (
              <p style={{ color: "#f88", fontSize: "0.8rem", marginTop: "0.4rem" }}>
                No playable streams detected in this file.
              </p>
            )}
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
          <div style={S.sectionTitle}>Playback Info</div>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
            <div>
              <label style={S.label}>Now Playing</label>
              <div style={{ fontFamily: "monospace", color: "#fff", fontSize: "0.95rem" }}>
                {status?.file ?? "—"}
              </div>
            </div>

            <div>
              <label style={S.label}>Stream Info</label>
              {status?.streams?.streams?.length > 0 ? (
                status.streams.streams.map((s, i) => <StreamLine key={i} s={s} />)
              ) : (
                <div style={{ color: "#666", fontSize: "0.85rem" }}>—</div>
              )}
            </div>

            <div>
              <span style={{
                display: "inline-block",
                padding: "0.25rem 0.75rem",
                borderRadius: "20px",
                background: running ? "#1a3a5c" : "#2a2a2a",
                color:      running ? "#4ca8f4" : "#666",
                fontSize: "0.78rem",
                fontWeight: 700,
              }}>
                ↻ Looping
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
