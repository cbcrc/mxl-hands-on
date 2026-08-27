// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { useState, useEffect, useRef } from "react";

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


// -- The log poll ---------------------------------------------------

const kMaxEvents = 2000;  // the console's own tail bound; the backend keeps 5000

function useLog(pollMs = 250) {
  const [events, setEvents] = useState([]);
  const [logError, setLogError] = useState(null);
  const sinceRef = useRef(0);

  useEffect(() => {
    let alive = true;

    async function poll() {
      try {
        const res = await fetch(API + "/log?since=" + sinceRef.current);
        const body = await res.json();
        if (!alive) return;             // unmounted while the fetch was in flight
        if (!Array.isArray(body)) {
          setLogError(body?.error ?? "HTTP " + res.status);
          return;
        }
        setLogError(null);
        if (body.length === 0) return;
        sinceRef.current = body[body.length - 1].seq;
        setEvents((prev) => prev.concat(body).slice(-kMaxEvents));
      } catch (e) {
        if (alive) setLogError(String(e));
      }
    }

    poll();                             // once now, so the first line isn't 250 ms late
    const id = setInterval(poll, pollMs);
    return () => { alive = false; clearInterval(id); };
  }, [pollMs]);

  return { events, logError };
}

// -- Console --------------------------------------------------------

const kOk = "#81c784", kWarn = "#ffb74d", kBad = "#e57373";

const monoStyle = { fontFamily: "ui-monospace, Menlo, monospace", fontSize: "0.8rem" };

const consoleStyle = {
  ...monoStyle,
  lineHeight: 1.45,
  height: "22rem",
  overflowY: "auto",
  background: "#111",
  borderRadius: "6px",
  padding: "0.5rem 0.75rem",
};

const headRowStyle = {
  ...monoStyle,
  color: "#888",
  whiteSpace: "pre",
  padding: "0 0.75rem 0.25rem",
  borderBottom: "1px solid #333",
  marginBottom: "0.25rem",
};

// The one place the column widths exist. The header and every event line are both
// built from it, so they cannot drift apart.
function row(seq, lane, step, call, status, dur) {
  return String(seq).padStart(5) +
         "  " + lane.padEnd(2) +
         "  " + step.padEnd(8) +
         "  " + call.padEnd(34) +
         "  " + status.padEnd(30) +
         dur.padStart(13);
}

function eventColor(e) {
  const s = e.status;
  if (s === undefined) return e.ok ? kOk : kBad;      // no ABI status: fall back to ok
  if (s === "MXL_STATUS_OK") return kOk;
  if (s.includes("OUT_OF_RANGE") || s.includes("TIMEOUT")) return kWarn;
  return kBad;
}

function EventLine({ e }) {
  return (
    <div style={{ color: eventColor(e), whiteSpace: "pre" }}>
      {row(e.seq, e.lane ?? "-", e.step_id ?? "", e.call ?? "", e.status ?? "",
            (e.duration_us ?? 0).toFixed(1) + " us")}
    </div>
  );
}
// -- APP ------------------------------------------------------------

export default function App() {
  const { events, logError } = useLog();
  const [domains, setDomains] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(API + "/domains");
        if (!res.ok) throw new Error("HTTP " + res.status);
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
        <img src="/cbc-logo.png" alt="CBC/Radio-Canada" style={{ height: "2.2rem"}} />
        <div>
          <h1>MXL ABI Tester</h1>
          <div style={subtitleStyle}>ALL 42 MXL C ABI calls, queued across two lanes.</div>
        </div>
      </header>
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
                    {d.buffer_depth_ms} ms{d.buffer_depth_is_default ? " (default)" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section style={sectionStyle}>
        <h2 style={{ marginBottom: "1rem" }}>Console</h2>
        {logError && <div style={{ color: kBad, marginBottom: "0.5rem" }}>{logError}</div>}
        <div style={headRowStyle}>{row("seq", "ln", "step", "call", "status", "duration")}</div>
        <div style={consoleStyle}>
          {events.length === 0 && <div style={{ color: "#888" }}>Waiting for events...</div>}
          {events.map((e) => <EventLine key={e.seq} e={e} />)}
        </div>
      </section>
    </div>
  );
}