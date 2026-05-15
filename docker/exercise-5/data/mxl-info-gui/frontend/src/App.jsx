import React, { useState, useEffect, useCallback } from "react";

const API = `http://${window.location.hostname}:9660`;

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

  // Poll every 500 ms when a flow is selected
  useEffect(() => {
    setFlowInfo(null);
    if (!flowUuid) return;
    fetchInfo();
    const id = setInterval(fetchInfo, 500);
    return () => clearInterval(id);
  }, [flowUuid, fetchInfo]);

  // Clear selection when domain changes
  useEffect(() => {
    setFlowUuid("");
    setFlowInfo(null);
  }, [selectedDomain]);

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
            {f.flow_label} ({f.flow_uuid.slice(0, 8)}…)
          </option>
        ))}
      </select>

      <div style={{ marginTop: "1rem" }}>
        <span style={labelStyle}>{label} Info</span>
        <FlowInfoDisplay info={flowUuid ? flowInfo : null} />
      </div>
    </div>
  );
}

// ── Main App ─────────────────────────────────────────────────────────────────

export default function App() {
  const [domains, setDomains]             = useState([]);
  const [selectedDomain, setSelectedDomain] = useState("");
  const [flows, setFlows]                 = useState([]);
  const [scanMsg, setScanMsg]             = useState("");

  // ── Domain helpers ──────────────────────────────────────────────────────────

  const fetchDomains = useCallback(async () => {
    try {
      const r = await fetch(`${API}/domains`);
      const d = await r.json();
      setDomains(d);
    } catch {
      // ignore
    }
  }, []);

  const triggerScan = async () => {
    setScanMsg("Scanning…");
    try {
      const r = await fetch(`${API}/get-domains`, { method: "POST" });
      const d = await r.json();
      setDomains(d);
      setScanMsg(`Found ${d.length} domain(s)`);
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

  // Fetch flows immediately when domain changes, then poll every 30 s
  useEffect(() => {
    setFlows([]);
    if (!selectedDomain) return;
    fetchFlows();
    const id = setInterval(fetchFlows, 30_000);
    return () => clearInterval(id);
  }, [selectedDomain, fetchFlows]);

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div style={{ maxWidth: "960px", width: "100%" }}>
      {/* Header */}
      <div style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ fontSize: "1.8rem", fontWeight: 700 }}>MXL Info GUI</h1>
        <p style={{ color: "#666", fontSize: "0.8rem", marginTop: "0.3rem" }}>
          Probe MXL domains and flows
        </p>
      </div>

      {/* ── Domain Management ──────────────────────────────────────────────── */}
      <div style={sectionStyle}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "1rem",
            marginBottom: "1rem",
          }}
        >
          <h2 style={{ fontSize: "1.1rem", fontWeight: 600 }}>Domains</h2>
          <button style={btnStyle} onClick={triggerScan}>
            Scan Domains
          </button>
          {scanMsg && (
            <span style={{ color: "#888", fontSize: "0.8rem" }}>{scanMsg}</span>
          )}
        </div>

        {/* Domain list – scales with content */}
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

        {/* Domain selector */}
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

      {/* ── MXL Flow List ──────────────────────────────────────────────────── */}
      <div style={sectionStyle}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "1rem",
            marginBottom: "1rem",
          }}
        >
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
          /* Scrollable after 20 rows (~560px) */
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
                {flows.map((f) => (
                  <tr key={f.flow_uuid}>
                    <td style={tdStyle}>{f.flow_uuid}</td>
                    <td style={{ ...tdStyle, fontFamily: "inherit" }}>
                      {f.flow_label}
                    </td>
                    <td style={{ ...tdStyle, fontFamily: "inherit" }}>
                      {f.flow_grouphint}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Flow 1 & Flow 2 side-by-side ──────────────────────────────────── */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "1rem",
        }}
      >
        <FlowPanel label="Flow 1" flows={flows} selectedDomain={selectedDomain} />
        <FlowPanel label="Flow 2" flows={flows} selectedDomain={selectedDomain} />
      </div>
    </div>
  );
}
