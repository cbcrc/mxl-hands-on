import React, { useState, useEffect, useCallback } from "react";

const API = "";

// ── Shared styles ────────────────────────────────────────────────────────────

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

const selectStyle = {
  width: "100%",
  padding: "0.5rem",
  background: "#2a2a2a",
  color: "#fff",
  border: "1px solid #444",
  borderRadius: "4px",
  fontSize: "1rem",
};

const btnStyle = {
  padding: "0.5rem 1.2rem",
  background: "#0d7c3e",
  color: "#fff",
  border: "none",
  borderRadius: "4px",
  cursor: "pointer",
  fontWeight: 600,
  fontSize: "0.9rem",
};

const tableStyle = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: "0.85rem",
};

const thStyle = {
  textAlign: "left",
  color: "#888",
  padding: "0.4rem 0.6rem",
  borderBottom: "1px solid #333",
  fontWeight: 600,
};

const tdStyle = {
  padding: "0.4rem 0.6rem",
  borderBottom: "1px solid #222",
  wordBreak: "break-all",
  fontFamily: "monospace",
};

const groupHeaderTdStyle = {
  padding: "0.35rem 0.6rem",
  background: "#252525",
  color: "#5b9bd5",
  fontFamily: "inherit",
  fontWeight: 700,
  fontSize: "0.78rem",
  letterSpacing: "0.05em",
  borderTop: "1px solid #2e2e2e",
};

const monoBlock = {
  background: "#111",
  borderRadius: "6px",
  padding: "0.8rem 1rem",
  fontFamily: "monospace",
  fontSize: "0.82rem",
  lineHeight: "1.6",
  marginTop: "0.6rem",
  color: "#c8e6c9",
};

// ── FlowInfoDisplay component ────────────────────────────────────────────────

function FlowInfoDisplay({ info }) {
  if (!info) return <div style={{ color: "#555", marginTop: "0.6rem" }}>No flow selected.</div>;
  const entries = Object.entries(info.fields || {});
  if (entries.length === 0)
    return <div style={{ color: "#555", marginTop: "0.6rem" }}>No data.</div>;
  return (
    <div style={monoBlock}>
      <div style={{ color: "#aaa", marginBottom: "0.4rem" }}>
        Flow: {info.flow_uuid}
      </div>
      {entries.map(([k, v]) => (
        <div key={k}>
          <span style={{ color: "#888" }}>{k}: </span>
          <span style={{ color: "#e0e0e0" }}>{v}</span>
        </div>
      ))}
    </div>
  );
}

// ── FlowSelector + FlowInfo panel ────────────────────────────────────────────

function FlowPanel({ label, flows, selectedDomain }) {
  const [flowUuid, setFlowUuid] = useState("");
  const [flowInfo, setFlowInfo] = useState(null);
  const [polling, setPolling]   = useState(false);

  const fetchInfo = useCallback(async () => {
    if (!selectedDomain || !flowUuid) return;
    try {
      const r = await fetch(
        `${API}/flow-info?domain_path=${encodeURIComponent(selectedDomain)}&flow_uuid=${encodeURIComponent(flowUuid)}`
      );
      const d = await r.json();
      setFlowInfo(d);
    } catch {
      // ignore transient errors
    }
  }, [selectedDomain, flowUuid]);

  // Fetch once when flow selected; start 500 ms interval when polling is on
  useEffect(() => {
    setFlowInfo(null);
    if (!flowUuid) return;
    fetchInfo();
    if (!polling) return;
    const id = setInterval(fetchInfo, 500);
    return () => clearInterval(id);
  }, [flowUuid, polling, fetchInfo]);

  // Reset when domain changes
  useEffect(() => {
    setFlowUuid("");
    setFlowInfo(null);
    setPolling(false);
  }, [selectedDomain]);

  const checkboxId = `poll-${label.replace(/\s+/g, "-").toLowerCase()}`;

  return (
    <div style={sectionStyle}>
      <label style={labelStyle}>{label} Selector</label>
      <select
        style={selectStyle}
        value={flowUuid}
        onChange={(e) => setFlowUuid(e.target.value)}
      >
        <option value="">-- Select {label} --</option>
        {flows.map((f) => (
          <option key={f.flow_uuid} value={f.flow_uuid}>
            {f.flow_label} — {f.flow_grouphint} ({f.flow_uuid.slice(0, 8)}…)
          </option>
        ))}
      </select>

      <div style={{ marginTop: "1rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "0.4rem" }}>
          <span style={{ ...labelStyle, marginBottom: 0 }}>{label} Info</span>
          <input
            type="checkbox"
            id={checkboxId}
            checked={polling}
            onChange={(e) => setPolling(e.target.checked)}
            style={{ width: "15px", height: "15px", cursor: "pointer", accentColor: "#0d7c3e" }}
          />
          <label
            htmlFor={checkboxId}
            style={{ fontSize: "0.78rem", color: polling ? "#4caf50" : "#666", cursor: "pointer" }}
          >
            Live update (0.5 s)
          </label>
        </div>
        <FlowInfoDisplay info={flowUuid ? flowInfo : null} />
      </div>
    </div>
  );
}

// ── Group flows by group-name prefix ─────────────────────────────────────────

function groupByGroupName(flows) {
  const groups = {};
  for (const f of flows) {
    const colonIdx = f.flow_grouphint.indexOf(":");
    const groupName =
      colonIdx >= 0 ? f.flow_grouphint.slice(0, colonIdx) : f.flow_grouphint || "(ungrouped)";
    if (!groups[groupName]) groups[groupName] = [];
    groups[groupName].push(f);
  }
  return groups;
}

// ── Main App ─────────────────────────────────────────────────────────────────

export default function App() {
  const [domains, setDomains]               = useState([]);
  const [selectedDomain, setSelectedDomain] = useState("");
  const [flows, setFlows]                   = useState([]);
  const [orphanFlows, setOrphanFlows]       = useState([]);
  const [scanMsg, setScanMsg]               = useState("");

  // ── Domain helpers ──────────────────────────────────────────────────────────

  const fetchDomains = useCallback(async () => {
    try {
      const r = await fetch(`${API}/domains`);
      const d = await r.json();
      setDomains(Array.isArray(d) ? d : []);
    } catch {
      // ignore
    }
  }, []);

  const triggerScan = async () => {
    setScanMsg("Scanning…");
    try {
      const r = await fetch(`${API}/get-domains`, { method: "POST" });
      const d = await r.json();
      setDomains(Array.isArray(d) ? d : []);
      setScanMsg(`Found ${Array.isArray(d) ? d.length : 0} domain(s)`);
    } catch {
      setScanMsg("Scan failed");
    }
  };

  // Poll domains every 30 s
  useEffect(() => {
    fetchDomains();
    const id = setInterval(fetchDomains, 30_000);
    return () => clearInterval(id);
  }, [fetchDomains]);

  // ── Flow helpers ────────────────────────────────────────────────────────────

  const fetchFlows = useCallback(async () => {
    if (!selectedDomain) return;
    try {
      const r = await fetch(
        `${API}/scan-domain?domain_path=${encodeURIComponent(selectedDomain)}`
      );
      const d = await r.json();
      setFlows(Array.isArray(d) ? d : []);
    } catch {
      // ignore
    }
  }, [selectedDomain]);

  const fetchOrphanFlows = useCallback(async () => {
    if (!selectedDomain) return;
    try {
      const r = await fetch(
        `${API}/orphan-flows?domain_path=${encodeURIComponent(selectedDomain)}`
      );
      const d = await r.json();
      setOrphanFlows(Array.isArray(d) ? d : []);
    } catch {
      setOrphanFlows([]);
    }
  }, [selectedDomain]);

  // Fetch flows + orphans when domain changes; poll every 30 s
  useEffect(() => {
    setFlows([]);
    setOrphanFlows([]);
    if (!selectedDomain) return;
    fetchFlows();
    fetchOrphanFlows();
    const id = setInterval(() => {
      fetchFlows();
      fetchOrphanFlows();
    }, 30_000);
    return () => clearInterval(id);
  }, [selectedDomain, fetchFlows, fetchOrphanFlows]);

  // ── Grouped flow view ───────────────────────────────────────────────────────

  const groupedFlows = groupByGroupName(flows);

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div style={{ maxWidth: "960px", width: "100%" }}>
      {/* Header */}
      <div style={{ marginBottom: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <img src="/cbc-logo.png" alt="CBC Radio-Canada" style={{ height: "2.2rem", objectFit: "contain" }} />
          <h1 style={{ fontSize: "1.8rem", fontWeight: 700, margin: 0 }}>MXL Info GUI</h1>
        </div>
        <p style={{ color: "#666", fontSize: "0.8rem", marginTop: "0.3rem" }}>
          Probe MXL domains and flows
        </p>
      </div>

      {/* ── Domain Management ──────────────────────────────────────────────── */}
      <div style={sectionStyle}>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1rem" }}>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 600 }}>Domains</h2>
          <button style={btnStyle} onClick={triggerScan}>
            Scan Domains
          </button>
          {scanMsg && (
            <span style={{ color: "#888", fontSize: "0.8rem" }}>{scanMsg}</span>
          )}
        </div>

        {domains.length === 0 ? (
          <p style={{ color: "#555", fontSize: "0.85rem", marginBottom: "1rem" }}>
            No domains found. Press "Scan Domains".
          </p>
        ) : (
          <div style={{ marginBottom: "1rem", overflowX: "auto" }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Domain UUID</th>
                  <th style={thStyle}>Path</th>
                </tr>
              </thead>
              <tbody>
                {domains.map((d) => (
                  <tr key={d.path}>
                    <td style={tdStyle}>{d.id}</td>
                    <td style={tdStyle}>{d.path}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <label style={labelStyle}>Select Domain</label>
        <select
          style={selectStyle}
          value={selectedDomain}
          onChange={(e) => setSelectedDomain(e.target.value)}
        >
          <option value="">-- Select Domain --</option>
          {domains.map((d) => (
            <option key={d.path} value={d.path}>
              {d.id} — {d.path}
            </option>
          ))}
        </select>
      </div>

      {/* ── MXL Flow List (grouped by group name) ──────────────────────────── */}
      <div style={sectionStyle}>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1rem" }}>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 600 }}>MXL Flows</h2>
          <button
            style={{ ...btnStyle, opacity: selectedDomain ? 1 : 0.4 }}
            onClick={fetchFlows}
            disabled={!selectedDomain}
          >
            Refresh Flows
          </button>
          {selectedDomain && (
            <span style={{ color: "#888", fontSize: "0.8rem" }}>
              {flows.length} flow(s)
            </span>
          )}
        </div>

        {!selectedDomain ? (
          <p style={{ color: "#555", fontSize: "0.85rem" }}>
            Select a domain above to list its flows.
          </p>
        ) : flows.length === 0 ? (
          <p style={{ color: "#555", fontSize: "0.85rem" }}>No flows found.</p>
        ) : (
          <div
            style={{
              overflowY: flows.length > 20 ? "auto" : "visible",
              maxHeight: flows.length > 20 ? "560px" : "none",
            }}
          >
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Flow UUID</th>
                  <th style={thStyle}>Label</th>
                  <th style={thStyle}>Group Hint</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(groupedFlows).map(([groupName, groupFlows]) => (
                  <React.Fragment key={groupName}>
                    <tr>
                      <td colSpan={3} style={groupHeaderTdStyle}>
                        {groupName}
                      </td>
                    </tr>
                    {groupFlows.map((f) => (
                      <tr key={f.flow_uuid}>
                        <td style={tdStyle}>{f.flow_uuid}</td>
                        <td style={{ ...tdStyle, fontFamily: "inherit" }}>{f.flow_label}</td>
                        <td style={{ ...tdStyle, fontFamily: "inherit" }}>{f.flow_grouphint}</td>
                      </tr>
                    ))}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Orphan Flows ───────────────────────────────────────────────────── */}
      {selectedDomain && (
        <div style={sectionStyle}>
          <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "0.5rem" }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600 }}>Orphan Flows</h2>
            <button
              style={{ ...btnStyle, opacity: 1 }}
              onClick={fetchOrphanFlows}
            >
              Refresh
            </button>
            <span style={{ color: "#888", fontSize: "0.8rem" }}>
              {orphanFlows.length} orphan(s)
            </span>
          </div>
          <p style={{ color: "#555", fontSize: "0.78rem", marginBottom: "0.75rem" }}>
            On-disk <code>.mxl-flow</code> directories not reported by{" "}
            <code>mxl-info -d</code> — inactive, leftover, or unreadable flows.
          </p>
          {orphanFlows.length === 0 ? (
            <p style={{ color: "#555", fontSize: "0.85rem" }}>No orphan flows found.</p>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>Flow UUID</th>
                    <th style={thStyle}>Label</th>
                    <th style={thStyle}>Group Hint</th>
                    <th style={thStyle}>Directory</th>
                  </tr>
                </thead>
                <tbody>
                  {orphanFlows.map((f) => (
                    <tr key={f.flow_uuid}>
                      <td style={tdStyle}>{f.flow_uuid}</td>
                      <td style={{ ...tdStyle, fontFamily: "inherit" }}>
                        {f.flow_label || <span style={{ color: "#444" }}>—</span>}
                      </td>
                      <td style={{ ...tdStyle, fontFamily: "inherit" }}>
                        {f.flow_grouphint || <span style={{ color: "#444" }}>—</span>}
                      </td>
                      <td style={tdStyle}>{f.directory}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Flow 1 & Flow 2 side-by-side ──────────────────────────────────── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
        <FlowPanel label="Flow 1" flows={flows} selectedDomain={selectedDomain} />
        <FlowPanel label="Flow 2" flows={flows} selectedDomain={selectedDomain} />
      </div>
    </div>
  );
}
