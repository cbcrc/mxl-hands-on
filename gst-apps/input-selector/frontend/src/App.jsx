import React, { useCallback, useEffect, useState } from "react";

const API = "";
const NUM_INPUTS = 3;

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
  const [inputs, setInputs] = useState(["none", "none", "none"]);
  const [grouphint,   setGrouphint]   = useState("Input-Selector");
  const [description, setDescription] = useState("selector-out-1");
  const [label,       setLabel]       = useState("input-selector-video");

  const [starting, setStarting]   = useState(false);
  const [error,    setError]      = useState("");
  const [formatErr, setFormatErr] = useState(null); // { errors: [], per_slot: [] }

  const running = status?.running === true;

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
    setInputs(["none", "none", "none"]);
    setFormatErr(null);
    loadFlows(path);
  };

  const rescanDomains = () =>
    fetch(`${API}/get-domains`, { method: "POST" })
      .then(r => r.json())
      .then(d => { if (Array.isArray(d)) setDomains(d); })
      .catch(() => {});

  const refreshFlows = () => { if (selectedDomain) loadFlows(selectedDomain); };

  const setSlotFlow = (idx, value) => {
    setInputs((prev) => {
      const next = [...prev];
      next[idx] = value;
      return next;
    });
    setFormatErr(null);
  };

  const handleStart = async () => {
    setError("");
    setFormatErr(null);
    setStarting(true);
    try {
      await post("/pipeline/start", {
        domain_path:      selectedDomain,
        input_flow_uuids: inputs.map((v) => (v === "none" ? null : v)),
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

  const switchActive = async (slot) => {
    try {
      await post("/pipeline/active-input", { slot });
      await fetchStatus();
    } catch (e) {
      setError(e.message);
    }
  };

  // ── Derived state ──────────────────────────────────────────────────────────

  const videoFlows = flows.filter(isVideoFlow);
  const anyMxlInput = inputs.some((v) => v !== "none");
  const canStart =
    !running &&
    !starting &&
    !!selectedDomain &&
    anyMxlInput &&
    description.trim() !== "" &&
    label.trim() !== "";

  const activeInput = status?.active_input ?? null;
  const slotKinds   = status?.slot_kinds ?? inputs.map((v) => (v !== "none" ? "mxl" : "black"));
  const slotUuids   = status?.input_flow_uuids ?? inputs.map((v) => (v !== "none" ? v : null));

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1.5rem" }}>
        <img src="/cbc-logo.png" alt="CBC Radio-Canada" style={{ height: "2.2rem" }} />
        <h1 style={{ fontSize: "1.6rem", fontWeight: 700 }}>
          MXL Input Selector
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

      {/* Format mismatch banner */}
      {formatErr && (
        <div style={{ background: "#3a1010", border: "1px solid #8b1a1a", borderRadius: "6px", padding: "0.75rem 1rem", marginBottom: "1rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
            <strong style={{ color: "#f88" }}>Input format error</strong>
            <button
              onClick={() => setFormatErr(null)}
              style={{ background: "transparent", border: "none", color: "#f88", cursor: "pointer", fontSize: "1.1rem" }}
              title="Dismiss"
            >
              ✕
            </button>
          </div>
          <ul style={{ color: "#f88", fontSize: "0.85rem", marginLeft: "1.2rem", marginBottom: "0.6rem" }}>
            {(formatErr.errors || []).map((e, i) => <li key={i}>{e}</li>)}
          </ul>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8rem" }}>
            <thead>
              <tr style={{ color: "#aaa" }}>
                <th style={{ textAlign: "left", padding: "0.2rem 0.5rem" }}>Slot</th>
                <th style={{ textAlign: "left", padding: "0.2rem 0.5rem" }}>Detected format</th>
              </tr>
            </thead>
            <tbody>
              {(formatErr.per_slot || []).map((s, i) => (
                <tr key={i} style={{ borderTop: "1px solid #5c1a1a" }}>
                  <td style={{ padding: "0.25rem 0.5rem", color: "#ccc" }}>Input {i + 1}</td>
                  <td style={{ padding: "0.25rem 0.5rem", color: "#ccc", fontFamily: "monospace" }}>{s || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
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

          {/* Input configuration table */}
          <div style={{ marginBottom: "0.85rem" }}>
            <label style={S.label}>
              Input Configuration
              {flowsLoading && <span style={{ color: "#555", marginLeft: "0.4rem" }}>(loading flows…)</span>}
            </label>
            <table style={{ width: "100%", borderCollapse: "collapse", background: "#222", borderRadius: "6px", overflow: "hidden" }}>
              <thead>
                <tr style={{ background: "#2a2a2a" }}>
                  <th style={{ padding: "0.5rem 0.6rem", textAlign: "left", color: "#888", fontSize: "0.78rem", fontWeight: 600, width: "90px" }}>Slot</th>
                  <th style={{ padding: "0.5rem 0.6rem", textAlign: "left", color: "#888", fontSize: "0.78rem", fontWeight: 600 }}>MXL Video Flow</th>
                </tr>
              </thead>
              <tbody>
                {[0, 1, 2].map((idx) => (
                  <tr key={idx}>
                    <td style={{ padding: "0.5rem 0.6rem", color: "#ccc", fontWeight: 600 }}>Input {idx + 1}</td>
                    <td style={{ padding: "0.5rem 0.6rem" }}>
                      <select
                        style={S.input}
                        value={inputs[idx]}
                        onChange={(e) => setSlotFlow(idx, e.target.value)}
                        disabled={running || !selectedDomain}
                      >
                        <option value="none">None — black fill</option>
                        {videoFlows.map((f) => (
                          <option key={f.flow_uuid} value={f.flow_uuid}>
                            {flowOptionLabel(f)}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
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

        {/* Active Input selector */}
        <div style={{ marginBottom: "1.25rem" }}>
          <label style={S.label}>Active Input</label>
          <div style={{ display: "flex", gap: "0.6rem" }}>
            {[0, 1, 2].map((idx) => {
              const isActive = activeInput === idx;
              const kind     = slotKinds[idx] || "black";
              const uuid     = slotUuids[idx];
              const isBlack  = kind !== "mxl";
              const btnDisabled = !running || isBlack;
              return (
                <button
                  key={idx}
                  onClick={() => switchActive(idx)}
                  disabled={btnDisabled}
                  title={isBlack ? "Black-fill slots cannot be switched to live" : undefined}
                  style={{
                    flex: 1,
                    padding: "0.9rem 0.75rem",
                    borderRadius: "6px",
                    border: isActive ? "2px solid #4caf50" : "2px solid #2a2a2a",
                    background: isActive ? "#1a3a1a" : "#222",
                    color: "#fff",
                    cursor: btnDisabled ? "not-allowed" : "pointer",
                    opacity: isBlack ? 0.45 : 1,
                    textAlign: "left",
                    transition: "border-color 0.15s, background 0.15s",
                  }}
                >
                  <div style={{ fontWeight: 700, fontSize: "0.95rem", marginBottom: "0.25rem" }}>
                    {isActive && "● "}Input {idx + 1}
                  </div>
                  <div style={{ fontSize: "0.78rem", color: "#aaa" }}>
                    {kind === "mxl" ? "MXL flow" : "⬛ Black fill (disabled)"}
                  </div>
                  <div style={{ fontSize: "0.72rem", color: "#666", fontFamily: "monospace", marginTop: "0.2rem" }}>
                    {uuid ? `${uuid.slice(0, 8)}…` : "—"}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Input status cards */}
        <div style={{ marginBottom: "1.25rem" }}>
          <label style={S.label}>Input Status</label>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
            {[0, 1, 2].map((idx) => {
              const isActive = activeInput === idx;
              const kind     = slotKinds[idx] || "black";
              const uuid     = slotUuids[idx];
              return (
                <div key={idx} style={{ display: "flex", alignItems: "center", fontSize: "0.85rem" }}>
                  <span style={connDot(isActive)} />
                  <span style={{ color: "#aaa", width: "72px" }}>Input {idx + 1}</span>
                  <span style={{ color: "#aaa", width: "110px", fontSize: "0.78rem" }}>
                    {kind === "mxl" ? "MXL flow" : "Black fill"}
                  </span>
                  <span style={{ fontFamily: "monospace", color: uuid ? "#ccc" : "#555" }}>
                    {uuid || "—"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Output flow info */}
        <div>
          <label style={S.label}>Output Flow</label>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem", fontSize: "0.85rem" }}>
            <div>
              <span style={{ color: "#aaa", display: "inline-block", width: "120px" }}>UUID:</span>
              <span style={{ fontFamily: "monospace", color: status?.output_flow_uuid ? "#ccc" : "#555" }}>
                {status?.output_flow_uuid || "—"}
              </span>
            </div>
            <div>
              <span style={{ color: "#aaa", display: "inline-block", width: "120px" }}>Format:</span>
              <span style={{ fontFamily: "monospace", color: "#ccc" }}>{fmtFormat(status?.format)}</span>
            </div>
            <div>
              <span style={{ color: "#aaa", display: "inline-block", width: "120px" }}>Status:</span>
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
