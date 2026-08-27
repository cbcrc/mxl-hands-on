// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { useState, useEffect } from "react";

const API = "";

// -- Shared styles -------------------------------------------------

const pageStyle = { width: "100%", maxWidth: "1400px" };

const headerStyle = {
  display: "flex",
  alignItems: "center",
  gap: "1rem",
  marginBottom: "1.5rem",
};

const subtitleStyle = { color: "#888", fontSize: "0.9rem" };

const sectionStyle = {
  background: "#1c1c1c",
  borderRadius: "8px",
  padding: "1.5rem",
  marginBottom: "1rem",
};

const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" };

const cellStyle = { textAlign: "left", padding: "0.5rem", borderBottom: "1px solid #333" };

// -- APP ------------------------------------------------------------

export default function App() {
  const [domains, setDomains] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(API + "/domains");
        if (!res.ok) throw new error("HTTP " + res.status);
        setDomains(await res.json());
      } catch (e) {
        setError(String(e));
      }
    }
    load();
  }, []);
  return (
    <div style={pageStyle}>
      <header style={headerStyle}>
        <section style={sectionStyle}>
          <h2 style={{ marginBottom: "1rem" }}>Domains</h2>
          {error && <div style={{ color: "#e57373" }}>{error}</div>}
          {!error && domains.length === 0 && <div style={{ color: "#888" }}>No domains found.</div>}
          {domains.length > 0 && (
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={cellStyle}>Label</th>
                  <th style={cellStyle}>ID</th>
                  <th style={cellStyle}>Path</th>
                  <th style={cellStyle}>Buffer depth</th>
                </tr>
              </thead>
              <tbody>
                {domains.map((d) => (
                  <tr key={d.id}>
                    <td style={cellStyle}>{d.label}</td>
                    <td style={cellStyle}>{d.id}</td>
                    <td style={cellStyle}>{d.path}</td>
                    <td style={cellStyle}>
                      {d.buffer_depth_ms} ms{d.buffer_depth_is_default ? " (default" : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <img src="/cbc-logo.png" alt="CBC/Radio-Canada" style={{ height: "2.2rem"}} />
        <div>
          <h1>MXL ABI Tester</h1>
          <div style={subtitleStyle}>ALL 42 MXL C ABI calls, queued across two lanes.</div>
        </div>
      </header>
    </div>
  );
}