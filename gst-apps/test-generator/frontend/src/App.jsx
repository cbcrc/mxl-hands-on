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

function Input({ label, value, onChange, disabled, type = "text", min, max, placeholder }) {
  return (
    <div>
      {label && <label style={S.label}>{label}</label>}
      <input
        type={type}
        min={min}
        max={max}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(type === "number" ? Number(e.target.value) : e.target.value)}
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

const LEVEL_TICKS = [-60, -40, -20, -10, 0];

function LevelFader({ value, onChange, disabled }) {
  const color =
    value === 0 ? "#f44" :
    value > -6  ? "#fa0" :
                  "#4caf50";
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "0.3rem" }}>
        <label style={S.label}>Audio Level</label>
        <span style={{ fontFamily: "monospace", fontWeight: 700, color, fontSize: "1.1rem" }}>
          {value.toFixed(1)} dBFS
        </span>
      </div>
      <input
        type="range"
        min={-60}
        max={0}
        step={0.5}
        value={value}
        disabled={disabled}
        style={{ width: "100%", accentColor: "#0d7c3e", opacity: disabled ? 0.4 : 1 }}
        onChange={(e) => onChange(parseFloat(e.target.value), false)}
        onMouseUp={(e)  => onChange(parseFloat(e.target.value), true)}
        onTouchEnd={(e) => onChange(parseFloat(e.target.value), true)}
      />
      <div style={{ position: "relative", height: "1.2em", marginTop: "2px" }}>
        {LEVEL_TICKS.map((t) => {
          const pct = ((t + 60) / 60) * 100;
          const transform =
            pct === 0   ? "none" :
            pct === 100 ? "translateX(-100%)" :
                          "translateX(-50%)";
          return (
            <span key={t} style={{ position: "absolute", left: `${pct}%`, transform, color: "#555", fontSize: "0.72rem", whiteSpace: "nowrap" }}>
              {t === 0 ? "0 dBFS" : t}
            </span>
          );
        })}
      </div>
    </div>
  );
}

// ── Audio Panel ───────────────────────────────────────────────────────────────

function AudioPanel({ flowNum, audioPatterns, status, disabled }) {
  const key = `audio${flowNum}`;
  const apiBase = `/audio/flow${flowNum}`;
  const state = status?.[key];

  const [levelDraft, setLevelDraft] = useState(state?.level_db ?? -20);

  // Sync level draft when pipeline restarts
  useEffect(() => {
    if (state) setLevelDraft(state.level_db);
  }, [status?.running]);

  const handleLevel = useCallback(async (val, commit) => {
    setLevelDraft(val);
    if (commit) {
      try { await post(`${apiBase}/level`, { db: val }); } catch {}
    }
  }, [apiBase]);

  const handlePattern = useCallback(async (pattern) => {
    try { await post(`${apiBase}/test-pattern`, { pattern }); } catch {}
  }, [apiBase]);

  return (
    <div style={S.card}>
      <div style={S.sectionTitle}>Audio Flow {flowNum}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
        <Select
          label="Test Pattern"
          value={state?.pattern ?? "1 kHz tone"}
          onChange={handlePattern}
          options={audioPatterns}
          disabled={disabled}
        />
        <LevelFader value={levelDraft} onChange={handleLevel} disabled={disabled} />
      </div>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

const DEFAULT_FLOWS = {
  video:  { active: true, description: "video-out-1",  label: "video-test-pattern"  },
  audio1: { active: true, description: "audio-out-1",  label: "audio-test-pattern-1", channels: 2 },
  audio2: { active: true, description: "audio-out-2",  label: "audio-test-pattern-2", channels: 2 },
};

export default function App() {
  // ── Fetched data
  const [status,       setStatus]       = useState(null);
  const [domains,      setDomains]      = useState([]);
  const [patterns,     setPatterns]     = useState({ video: [], audio: [] });
  const [options,      setOptions]      = useState({ resolutions: [], framerates: [] });

  // ── Setup form state
  const [domain,     setDomain]     = useState("");
  const [grouphint,  setGrouphint]  = useState("Test-Generator");
  const [resolution, setResolution] = useState("1920x1080");
  const [framerate,  setFramerate]  = useState("30");
  const [flows,      setFlows]      = useState(DEFAULT_FLOWS);

  // ── Operation: video
  const [identDraft, setIdentDraft] = useState("");
  const identApplied = useRef(false);

  // ── Error
  const [error, setError] = useState("");

  const running = status?.running ?? false;

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
        const [d, p, o] = await Promise.all([
          fetch(`${API}/domains`).then((r) => r.json()),
          fetch(`${API}/patterns`).then((r) => r.json()),
          fetch(`${API}/options`).then((r) => r.json()),
        ]);
        setDomains(d.domains ?? []);
        setPatterns(p);
        setOptions(o);
        if (d.domains?.length > 0) setDomain(d.domains[0].path);
      } catch {}
    })();
    fetchStatus();
    const id = setInterval(fetchStatus, 2000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  // Sync ident draft on first run
  useEffect(() => {
    if (running && !identApplied.current) {
      setIdentDraft(status?.video?.ident ?? "");
      identApplied.current = true;
    }
    if (!running) identApplied.current = false;
  }, [running]);

  // ── Setup validation ──────────────────────────────────────────────────────

  const canStart = !running &&
    domain !== "" &&
    Object.entries(flows).every(
      ([, f]) => !f.active || (f.description.trim() !== "" && f.label.trim() !== "")
    );

  // ── Handlers ──────────────────────────────────────────────────────────────

  const updateFlow = (key, field, value) =>
    setFlows((prev) => ({ ...prev, [key]: { ...prev[key], [field]: value } }));

  const handleStart = async () => {
    setError("");
    try {
      await post("/pipeline/start", {
        domain,
        grouphint,
        resolution,
        framerate,
        video:  flows.video,
        audio1: flows.audio1,
        audio2: flows.audio2,
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

  const handleVideoPattern = async (pattern) => {
    try { await post("/video/test-pattern", { pattern }); } catch {}
  };

  const handleTimecode = async (enabled) => {
    try { await post("/video/timecode", { enabled }); } catch {}
  };

  const handleIdent = async () => {
    try { await post("/video/ident", { text: identDraft }); } catch {}
  };

  // ── Domain options ────────────────────────────────────────────────────────

  const domainOptions = domains.map((d) => ({
    value: d.path,
    label: `${d.path}  (${(d.id || "").slice(0, 8)}…)`,
  }));

  // ── Flow table row ────────────────────────────────────────────────────────

  const FlowRow = ({ flowKey, label, hasChannels }) => {
    const f = flows[flowKey];
    return (
      <tr>
        <td style={{ padding: "0.4rem 0.5rem", color: "#ccc", whiteSpace: "nowrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: running ? "not-allowed" : "pointer" }}>
            <input
              type="checkbox"
              checked={f.active}
              onChange={(e) => !running && updateFlow(flowKey, "active", e.target.checked)}
              disabled={running}
              style={{ width: "16px", height: "16px" }}
            />
            {label}
          </label>
        </td>
        <td style={{ padding: "0.4rem 0.5rem", width: "80px" }}>
          {hasChannels ? (
            <input
              type="number"
              min={1}
              max={64}
              style={{ ...S.input, ...(running ? S.inputDisabled : {}) }}
              value={f.channels}
              onChange={(e) => updateFlow(flowKey, "channels", Math.min(64, Math.max(1, Number(e.target.value))))}
              disabled={running}
            />
          ) : (
            <span style={{ color: "#555", paddingLeft: "0.5rem" }}>—</span>
          )}
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
            MXL Test Generator
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

          {/* Resolution + framerate */}
          <div style={S.row}>
            <div style={S.col}>
              <Select
                label="Resolution"
                value={resolution}
                onChange={setResolution}
                options={options.resolutions.length > 0 ? options.resolutions : ["1920x1080"]}
                disabled={running}
              />
            </div>
            <div style={S.col}>
              <Select
                label="Frame Rate"
                value={framerate}
                onChange={setFramerate}
                options={options.framerates.length > 0 ? options.framerates : ["30"]}
                disabled={running}
              />
            </div>
          </div>

          {/* Flow configuration table */}
          <div>
            <label style={S.label}>Flow Configuration</label>
            <table style={{ width: "100%", borderCollapse: "collapse", background: "#222", borderRadius: "6px", overflow: "hidden" }}>
              <thead>
                <tr style={{ background: "#2a2a2a" }}>
                  <th style={{ padding: "0.4rem 0.5rem", textAlign: "left", color: "#888", fontSize: "0.78rem", fontWeight: 600 }}>Flow</th>
                  <th style={{ padding: "0.4rem 0.5rem", textAlign: "left", color: "#888", fontSize: "0.78rem", fontWeight: 600, width: "80px" }}>Channels</th>
                  <th style={{ padding: "0.4rem 0.5rem", textAlign: "left", color: "#888", fontSize: "0.78rem", fontWeight: 600 }}>Description</th>
                  <th style={{ padding: "0.4rem 0.5rem", textAlign: "left", color: "#888", fontSize: "0.78rem", fontWeight: 600 }}>Label</th>
                </tr>
              </thead>
              <tbody>
                <FlowRow flowKey="video"  label="Video"        hasChannels={false} />
                <FlowRow flowKey="audio1" label="Audio Flow 1" hasChannels={true}  />
                <FlowRow flowKey="audio2" label="Audio Flow 2" hasChannels={true}  />
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
          <div style={S.sectionTitle}>Video</div>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <Select
              label="Test Pattern"
              value={status?.video?.pattern ?? "100% bars"}
              onChange={handleVideoPattern}
              options={patterns.video.length > 0 ? patterns.video : ["100% bars"]}
              disabled={!running}
            />

            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
              <input
                type="checkbox"
                id="timecode"
                checked={status?.video?.timecode ?? true}
                onChange={(e) => handleTimecode(e.target.checked)}
                disabled={!running}
                style={{ width: "17px", height: "17px", cursor: running ? "pointer" : "not-allowed" }}
              />
              <label htmlFor="timecode" style={{ cursor: running ? "pointer" : "not-allowed", color: "#ccc" }}>
                Burn-in Timecode
              </label>
            </div>

            <div>
              <label style={S.label}>Ident</label>
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <input
                  style={{ ...S.input, flex: 1 }}
                  type="text"
                  placeholder="e.g. Camera 1"
                  value={identDraft}
                  onChange={(e) => setIdentDraft(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleIdent()}
                  disabled={!running}
                />
                <button
                  style={btn("primary", !running)}
                  onClick={handleIdent}
                  disabled={!running}
                >
                  Apply
                </button>
              </div>
            </div>
          </div>
        </div>

        <AudioPanel
          flowNum={1}
          audioPatterns={patterns.audio.length > 0 ? patterns.audio : ["1 kHz tone"]}
          status={status}
          disabled={!running}
        />

        <AudioPanel
          flowNum={2}
          audioPatterns={patterns.audio.length > 0 ? patterns.audio : ["1 kHz tone"]}
          status={status}
          disabled={!running}
        />
      </div>
    </div>
  );
}
