import React, { useState, useEffect, useCallback } from "react";

const API = `http://${window.location.hostname}:9630`;

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

// Input button: 3 visual states
//   connected=false → grey, dimmed
//   selected=true   → blue outline (pre-selected, not yet taken)
//   active=true      → solid green tally
const inputBtnStyle = ({ connected, selected, active }) => ({
  flex: 1,
  padding: "1.2rem 0.5rem",
  borderRadius: "6px",
  cursor: connected ? "pointer" : "default",
  fontWeight: 700,
  fontSize: "1rem",
  transition: "all 0.15s",
  position: "relative",
  border: active
    ? "2px solid #4caf50"
    : selected
    ? "2px solid #2196f3"
    : "2px solid #333",
  background: active
    ? "#0d4a1f"
    : selected
    ? "#0d2a4a"
    : connected
    ? "#2a2a2a"
    : "#1a1a1a",
  color: active ? "#4caf50" : selected ? "#2196f3" : connected ? "#eee" : "#555",
  opacity: connected ? 1 : 0.5,
});

const connDotStyle = (connected) => ({
  display: "inline-block",
  width: "8px",
  height: "8px",
  borderRadius: "50%",
  background: connected ? "#4caf50" : "#555",
  marginRight: "6px",
});

const takeBtn = (ready) => ({
  width: "100%",
  padding: "1rem",
  background: ready ? "#b8860b" : "#2a2a2a",
  color: ready ? "#fff" : "#555",
  border: `2px solid ${ready ? "#daa520" : "#333"}`,
  borderRadius: "6px",
  fontSize: "1.2rem",
  fontWeight: 700,
  cursor: ready ? "pointer" : "default",
  letterSpacing: "0.08em",
  transition: "all 0.15s",
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

  const post = async (path, body = {}) => {
    try {
      await fetch(`${API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      fetchStatus();
    } catch {}
  };

  const slots    = status?.slots ?? { "1": null, "2": null, "3": null };
  const selected = status?.selected_input ?? null;
  const active   = status?.active_input ?? null;
  const ready    = selected !== null;  // Take is available when something is pre-selected

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ fontSize: "1.6rem", fontWeight: 700 }}>
          MXL Input Selector
          <span style={statusBadge(active !== null)}>
            {active !== null ? `● INPUT ${active} ON AIR` : "○ NO OUTPUT"}
          </span>
        </h1>
        {status && (
          <p style={{ color: "#555", fontSize: "0.75rem", marginTop: "0.4rem" }}>
            Output flow: {status.output_flow_id || "—"}
          </p>
        )}
      </div>

      {/* Input buttons */}
      <div style={sectionStyle}>
        <span style={labelStyle}>Input Select</span>
        <div style={{ display: "flex", gap: "0.75rem" }}>
          {[1, 2, 3].map((n) => {
            const slot      = slots[String(n)] ?? {};
            const connected = slot.connected ?? false;
            const isSelected = selected === n;
            const isActive   = active === n;
            return (
              <button
                key={n}
                style={inputBtnStyle({ connected, selected: isSelected, active: isActive })}
                onClick={() => connected && post("/input-select", { input: n })}
                title={connected ? `Flow: ${slot.flow_id}` : "No flow connected"}
              >
                <span style={connDotStyle(connected)} />
                INPUT {n}
                {isActive && (
                  <div style={{ fontSize: "0.65rem", marginTop: "0.3rem", color: "#4caf50" }}>
                    ● ON AIR
                  </div>
                )}
                {isSelected && !isActive && (
                  <div style={{ fontSize: "0.65rem", marginTop: "0.3rem", color: "#2196f3" }}>
                    SELECTED
                  </div>
                )}
                {!connected && (
                  <div style={{ fontSize: "0.65rem", marginTop: "0.3rem", color: "#555" }}>
                    NO FLOW
                  </div>
                )}
              </button>
            );
          })}
        </div>
        <p style={{ color: "#555", fontSize: "0.72rem", marginTop: "0.6rem" }}>
          Click an input to pre-select it, then press Take to switch output.
          Inputs only become available once an NMOS IS-05 connection assigns a flow.
        </p>
      </div>

      {/* Take button */}
      <div style={sectionStyle}>
        <button
          style={takeBtn(ready)}
          onClick={() => ready && post("/take")}
        >
          ▶ TAKE {selected !== null ? `(Input ${selected} → Output)` : ""}
        </button>
      </div>

      {/* Slot detail */}
      <div style={sectionStyle}>
        <span style={labelStyle}>Slot Details</span>
        {[1, 2, 3].map((n) => {
          const slot = slots[String(n)] ?? {};
          return (
            <div
              key={n}
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "0.35rem 0",
                borderBottom: n < 3 ? "1px solid #2a2a2a" : "none",
                fontSize: "0.8rem",
              }}
            >
              <span style={{ color: "#aaa" }}>Input {n}</span>
              <span style={{ color: slot.connected ? "#ccc" : "#444", fontFamily: "monospace" }}>
                {slot.flow_id || "—"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
