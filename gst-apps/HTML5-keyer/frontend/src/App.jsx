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
  caption: {
    color: "#666",
    fontSize: "0.74rem",
    marginTop: "0.3rem",
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

const disabledOverlay = (disabled) =>
  disabled ? { opacity: 0.4, pointerEvents: "none" } : {};

// ── Helpers ───────────────────────────────────────────────────────────────────

function flowOptionLabel(f) {
  const prefix = f.flow_uuid.slice(0, 8);
  const desc   = f.description || "";
  const label  = f.flow_label  || "";
  const gh     = f.flow_grouphint || "";
  return `(${prefix}…) ${desc || label}${
    desc && label && desc !== label ? ` — ${label}` : ""
  } [${gh}]`;
}

function flowRole(f) {
  const parts = (f.flow_grouphint || "").split(":");
  return parts.length > 1 ? parts.slice(1).join(":").trim().toLowerCase() : "";
}

const isVideoFlow = (f) => flowRole(f).includes("video");

function fmtFormat(fmt) {
  if (!fmt) return "—";
  const gr = fmt.grain_rate;
  return `${fmt.frame_width}×${fmt.frame_height} @ ${gr.numerator}/${gr.denominator} (${fmt.interlace_mode})`;
}

const isValidUrl = (s) => /^https?:\/\/\S+/i.test((s || "").trim());

async function post(path, body) {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = d.detail;
    if (detail && typeof detail === "object") {
      const err = new Error(detail.detail || "request failed");
      err.payload = detail;
      throw err;
    }
    throw new Error(typeof detail === "string" ? detail : r.statusText);
  }
  return d;
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  // Fetched state
  const [domains, setDomains]           = useState([]);
  const [flows,   setFlows]             = useState([]);
  const [flowsLoading, setFlowsLoading] = useState(false);
  const [status,  setStatus]            = useState(null);

  // Setup form state
  const [selectedDomain, setSelectedDomain] = useState("");
  const [inputFlow,   setInputFlow]   = useState("");
  const [html5Url,    setHtml5Url]    = useState("");
  const [grouphint,   setGrouphint]   = useState("HTML5-Keyer");
  const [description, setDescription] = useState("keyer-out-1");
  const [label,       setLabel]       = useState("html5-keyer-video");

  const [starting, setStarting]   = useState(false);
  const [keyBusy,  setKeyBusy]    = useState(false);
  const [error,    setError]      = useState("");
  const [formatErr, setFormatErr] = useState(null);

  const running = status?.running === true;
  const keyOn   = status?.key_on === true;

  // ── Data fetching ──────────────────────────────────────────────────────────

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/pipeline/status`);
      setStatus(await r.json());
    } catch {}
  }, []);

  const loadFlows = useCallback((path) => {
    if (!path) { setFlows([]); return; }
    setFlowsLoading(true);
    fetch(`${API}/scan-domain?domain_path=${encodeURIComponent(path)}`)
      .then(r => r.json())
      .then(d => setFlows(Array.isArray(d) ? d : []))
      .catch(() => setFlows([]))
      .finally(() => setFlowsLoading(false));
  }, []);

  useEffect(() => {
    fetch(`${API}/domains`)
      .then(r => r.json())
      .then(d => {
        if (Array.isArray(d) && d.length > 0) {
          setDomains(d);
          setSelectedDomain(d[0].path);
          loadFlows(d[0].path);
        } else {
          setDomains([]);
        }
      })
      .catch(() => {});
    fetchStatus();
    const id = setInterval(fetchStatus, 2000);
    return () => clearInterval(id);
  }, [fetchStatus, loadFlows]);

  // ── Handlers ───────────────────────────────────────────────────────────────

  const handleDomainChange = (path) => {
    setSelectedDomain(path);
    setInputFlow("");
    setFormatErr(null);
    loadFlows(path);
  };

  const rescanDomains = () =>
    fetch(`${API}/get-domains`, { method: "POST" })
      .then(r => r.json())
      .then(d => { if (Array.isArray(d)) setDomains(d); })
      .catch(() => {});

  const refreshFlows = () => { if (selectedDomain) loadFlows(selectedDomain); };

  const handleStart = async () => {
    setError("");
    setFormatErr(null);
    setStarting(true);
    try {
      await post("/pipeline/start", {
        domain_path:     selectedDomain,
        input_flow_uuid: inputFlow,
        html5_url:       html5Url.trim(),
        grouphint,
        description,
        label,
      });
      await fetchStatus();
    } catch (e) {
      if (e.payload && e.payload.errors) {
        setFormatErr(e.payload);
      } else {
        setError(e.message);
      }
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    setError("");
    setFormatErr(null);
    try {
      await post("/pipeline/stop");
      await fetchStatus();
    } catch (e) {
      setError(e.message);
    }
  };

  const toggleKey = async () => {
    setError("");
    setKeyBusy(true);
    try {
      await post("/pipeline/key", { on: !keyOn });
      await fetchStatus();
    } catch (e) {
      setError(e.message);
    } finally {
      setKeyBusy(false);
    }
  };

  // ── Derived state ──────────────────────────────────────────────────────────

  const videoFlows = flows.filter(isVideoFlow);
  const urlValid   = isValidUrl(html5Url);
  const canStart =
    !running &&
    !starting &&
    !!selectedDomain &&
    !!inputFlow &&
    urlValid &&
    description.trim() !== "" &&
    label.trim() !== "";

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1.5rem" }}>
        <img src="/cbc-logo.png" alt="CBC Radio-Canada" style={{ height: "2.2rem" }} />
        <h1 style={{ fontSize: "1.6rem", fontWeight: 700 }}>
          MXL HTML5 Keyer
          <span style={badge(running)}>
            {running ? "● PUBLISHING" : "○ STOPPED"}
          </span>
        </h1>
      </div>

      {/* Plain error banner */}
      {error && (
        <div style={{ background: "#3a1010", border: "1px solid #8b1a1a", borderRadius: "6px", padding: "0.6rem 1rem", marginBottom: "1rem", color: "#f88" }}>
          {error}
        </div>
      )}

      {/* Format-read error banner */}
      {formatErr && (
        <div style={{ background: "#3a1010", border: "1px solid #8b1a1a", borderRadius: "6px", padding: "0.75rem 1rem", marginBottom: "1rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
            <strong style={{ color: "#f88" }}>Input flow format error</strong>
            <button
              onClick={() => setFormatErr(null)}
              style={{ background: "transparent", border: "none", color: "#f88", cursor: "pointer", fontSize: "1.1rem" }}
              title="Dismiss"
            >
              ✕
            </button>
          </div>
          <ul style={{ color: "#f88", fontSize: "0.85rem", marginLeft: "1.2rem", marginBottom: "0.4rem" }}>
            {(formatErr.errors || []).map((e, i) => <li key={i}>{e}</li>)}
          </ul>
          {formatErr.input_flow_uuid && (
            <div style={{ color: "#caa", fontSize: "0.78rem", fontFamily: "monospace" }}>
              Input UUID: {formatErr.input_flow_uuid}
            </div>
          )}
        </div>
      )}

      {/* ── Section 1: Setup ─────────────────────────────────────────────── */}
      <div style={S.card}>
        <div style={S.sectionTitle}>1 — Setup</div>

        <div style={disabledOverlay(running)}>
          {/* Domain row */}
          <div style={{ ...S.row, marginBottom: "0.85rem" }}>
            <div style={S.col}>
              <label style={S.label}>MXL Domain</label>
              <select
                style={S.input}
                value={selectedDomain}
                onChange={(e) => handleDomainChange(e.target.value)}
                disabled={running}
              >
                <option value="">— select a domain —</option>
                {domains.map((d) => (
                  <option key={d.path} value={d.path}>
                    {d.label || d.path}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <button style={btn("primary", running)} onClick={rescanDomains} disabled={running}>
                Scan Domains
              </button>
            </div>
            <div>
              <button
                style={btn("primary", running || !selectedDomain)}
                onClick={refreshFlows}
                disabled={running || !selectedDomain}
              >
                Refresh Flows
              </button>
            </div>
          </div>

          {/* MXL background input flow */}
          <div style={{ marginBottom: "0.85rem" }}>
            <label style={S.label}>
              MXL Background Input
              {flowsLoading && <span style={{ color: "#555", marginLeft: "0.4rem" }}>(loading flows…)</span>}
            </label>
            <select
              style={S.input}
              value={inputFlow}
              onChange={(e) => { setInputFlow(e.target.value); setFormatErr(null); }}
              disabled={running || !selectedDomain}
            >
              <option value="">— select an MXL video flow —</option>
              {videoFlows.map((f) => (
                <option key={f.flow_uuid} value={f.flow_uuid}>
                  {flowOptionLabel(f)}
                </option>
              ))}
            </select>
            <div style={S.caption}>
              The output flow's raster, frame rate, and interlace mode are derived from this input.
            </div>
          </div>

          {/* HTML5 graphics URL */}
          <div style={{ marginBottom: "0.85rem" }}>
            <label style={S.label}>HTML5 Graphics URL</label>
            <input
              type="text"
              placeholder="http://spx-server:5660/renderer/"
              style={{
                ...S.input,
                borderColor: html5Url && !urlValid ? "#8b1a1a" : "#444",
              }}
              value={html5Url}
              onChange={(e) => setHtml5Url(e.target.value)}
              disabled={running}
            />
            <div style={S.caption}>
              Pages must allow embedding (no <code>X-Frame-Options: deny</code>) and should
              render with a transparent background so the alpha channel keys correctly.
            </div>
          </div>

          {/* Output flow configuration */}
          <div style={{ ...S.row, marginBottom: "0.85rem" }}>
            <div style={S.col}>
              <label style={S.label}>Group Hint</label>
              <input
                style={S.input}
                value={grouphint}
                onChange={(e) => setGrouphint(e.target.value)}
                disabled={running}
              />
            </div>
            <div style={S.col}>
              <label style={S.label}>Output Description</label>
              <input
                style={S.input}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={running}
              />
            </div>
            <div style={S.col}>
              <label style={S.label}>Output Label</label>
              <input
                style={S.input}
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                disabled={running}
              />
            </div>
          </div>
        </div>

        {/* Start / Stop — outside disabled overlay so it always works */}
        <div>
          {!running ? (
            <button style={btn("success", !canStart)} onClick={handleStart} disabled={!canStart}>
              {starting ? "Starting…" : "▶ Start Pipeline"}
            </button>
          ) : (
            <button style={btn("danger")} onClick={handleStop}>■ Stop Pipeline</button>
          )}
        </div>
      </div>

      {/* ── Section 2: Operation ──────────────────────────────────────────── */}
      <div style={{ ...S.card, ...disabledOverlay(!running) }}>
        <div style={S.sectionTitle}>2 — Operation</div>

        {/* Key ON/OFF toggle — the only interactive control while running */}
        <div style={{ marginBottom: "1.5rem", textAlign: "center" }}>
          <button
            onClick={toggleKey}
            disabled={!running || keyBusy}
            style={{
              padding: "1rem 2.5rem",
              fontSize: "1.4rem",
              fontWeight: 700,
              borderRadius: "8px",
              border: keyOn ? "2px solid #4caf50" : "2px solid #555",
              background: keyOn ? "#1a5c2a" : "#2a2a2a",
              color: keyOn ? "#a8f4b6" : "#bbb",
              cursor: !running || keyBusy ? "not-allowed" : "pointer",
              minWidth: "260px",
              transition: "background 0.15s, border-color 0.15s, color 0.15s",
            }}
          >
            ● Key {keyOn ? "ON" : "OFF"}
          </button>
        </div>

        {/* Status panel */}
        <div>
          <label style={S.label}>Status</label>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem", fontSize: "0.85rem" }}>
            <div style={{ display: "flex", alignItems: "center" }}>
              <span style={connDot(running)} />
              <span style={{ color: "#aaa", display: "inline-block", width: "170px" }}>Background input:</span>
              <span
                style={{ fontFamily: "monospace", color: status?.input_flow_uuid ? "#ccc" : "#555" }}
                title={status?.input_flow_uuid || ""}
              >
                {status?.input_flow_uuid ? `${status.input_flow_uuid.slice(0, 8)}…` : "—"}
              </span>
            </div>
            <div>
              <span style={{ color: "#aaa", display: "inline-block", width: "170px", marginLeft: "18px" }}>HTML5 overlay URL:</span>
              <span
                style={{
                  color: status?.html5_url ? "#ccc" : "#555",
                  maxWidth: "560px",
                  display: "inline-block",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  verticalAlign: "bottom",
                }}
                title={status?.html5_url || ""}
              >
                {status?.html5_url || "—"}
              </span>
            </div>
            <div>
              <span style={{ color: "#aaa", display: "inline-block", width: "170px", marginLeft: "18px" }}>Output flow UUID:</span>
              <span style={{ fontFamily: "monospace", color: status?.output_flow_uuid ? "#ccc" : "#555" }}>
                {status?.output_flow_uuid || "—"}
              </span>
            </div>
            <div>
              <span style={{ color: "#aaa", display: "inline-block", width: "170px", marginLeft: "18px" }}>Format:</span>
              <span style={{ fontFamily: "monospace", color: "#ccc" }}>{fmtFormat(status?.format)}</span>
            </div>
            <div>
              <span style={{ color: "#aaa", display: "inline-block", width: "170px", marginLeft: "18px" }}>Status:</span>
              <span style={badge(running)}>{running ? "● PUBLISHING" : "○ STOPPED"}</span>
            </div>
          </div>
        </div>
      </div>

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
