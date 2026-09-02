// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { sectionStyle, tableStyle, cellStyle, chipStyle, monoStyle, kOk, kBad } from "./styles";

const API = "";

const help = {
  run: "Start every lane. Each lane is its own thread with its own cursor, and there " +
       "is no barrier between them: cross-lane ordering is delay_before_ms only.",
  pause: "Stop after the current step. The step in flight finishes, its delay " +
         "included, so pause is not instant.",
  reset: "Destroy every handle and rewind every lane to step 0. Releasing a writer " +
         "can delete the flow it created, so this is not a cheap undo.",
  step: "Run one step of this lane, inside this HTTP request. Disable while running: " +
        "stepOnce has no guard against the lane thread, so stepping here would interleave " +
        "with the thread's own stepping and advance the lane at a moment nothing records.",
  scale: "Multiplies delay_before_ms only -- a paced step aims at its grain's own OTS and " +
         "ignores the scale entirely. Sent with run: editing it mid-run changes nothing " +
         "until the next run. The server ignores anything <= 0, silently.",
};

// The order Engine::reset releases in (engine.cpp:1273), not alphabetical: a writer or
// reader must be gone before its instance, and sync group holds raw reader pointers.
// Read the table top to bottom and you are reading the order reset will destroy them in.
const kKindOrder = ["grain", "sync_group", "flow_writer", "flow_reader", "instance"];

// indexOf gives -1 for a kind not listed -- registry.cpp:18 can return "unknown" -- and
// -1 would sort it silently to to top, ahead of the grains. Unknown goes last instead.
const rank = (k) => { const i = kKindOrder.indexOf(k); return (i < 0) ? kKindOrder.length : i; };

export default function Transport({ state, apply }) {
  const [msg, setMsg] = useState(null);
  const running = state.running ?? false;
  const [scale, setScale] = useState("1");
  const lanes = state.lanes ?? {};
  const handles = state.handles ?? {};
  const nameOf = Object.fromEntries(Object.entries(handles).map(([n, h]) => [h.ptr, n]));
  const rows = Object.entries(handles).sort((a, b) => rank(a[1].kind) - rank(b[1].kind));
  const scaleOk = Number(scale) > 0;      // "" and "abc" both give NaN, and NaN > 0 is false

  // Its own copy of post(), not shared with the builder's: this one feeds the
  // response into apply() and returns nothing, that one returns ok for sequencing.
  // Two similar functions, factored if a third ever appears.
  // done(doc) -> the success message, or the omitted for "say nothing". The state word
  // beside the buttons already reports running/idle, so only /reset has a news that
  // state cannot carry: the handles it destroyed.
  async function post(path, body, done) {
    try {
      const res = await fetch(API + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: (body === undefined) ? undefined : JSON.stringify(body),
      });
      const doc = await res.json();
      if (!res.ok) {
        setMsg({text: doc.error ?? ("HTTP " + res.status), ok: false });
        return;
      }
      apply(doc);     // every transport verb answers with engine.state()
      setMsg(done ? { text: done(doc), ok: true } : null);
    } catch (e) {
      setMsg({ text: String(e), ok: false });
    }
  }

  return (
    <section style={sectionStyle}>
      <h2 style={{ marginBottom: "1rem" }}>Transport</h2>
      <button type="button" style={chipStyle(running)} title={running ? help.pause : help.run}
              onClick={() => running ? post("/pause")
                                     : post("/run", scaleOk ? { delay_scale: Number(scale) } : {})}>
        {running ? "pause" : "run"}</button>
      <button type="button" style={chipStyle(false)} title={help.reset}
              onClick={() => post("/reset", undefined, 
                                  (d) => "released " + (d.released?.join(" ") || "nothing"))}>
        reset</button>
      <label style={{ ...monoStyle, marginLeft: "1rem", color: "#888" }} title={help.scale}>
        delay x <input value={scale} onChange={(e) => setScale(e.target.value)}
                style={{ ...monoStyle, width: "4rem", background: "#111", color: "#eee",
                         borderRadius: "4px", padding: "0.15rem 0.35rem",
                         border: "1px solid " + (scaleOk ? "#333" : kBad) }} />    
      </label>
      <span style={{ ...monoStyle, marginLeft: "0.5rem", color: "#666" }}>
        (server {state.delay_scale ?? "?"})</span>
      <span style={{ ...monoStyle, marginLeft: "1rem", color: running ? kOk : "#888" }}>
        {running ? "running" : "idle"}</span>
      {msg && <span style={{ ...monoStyle, marginLeft: "0.75rem",
                             color: msg.ok ? kOk : kBad }}>{msg.text}</span>}
      <table style={{ ...tableStyle, width: "auto", marginTop: "1rem" }}>
        <tbody>
          {Object.entries(lanes).map(([laneName, ln]) => {
            const off = running || ln.next >= ln.steps;
            return (
              <tr key={laneName}>
                <td style={{ ...cellStyle, ...monoStyle }}>{laneName}</td>
                <td style={{ ...cellStyle, ...monoStyle, color: "#888" }}>
                  {ln.next} / {ln.steps}</td>
                <td style={{ ...cellStyle, ...monoStyle, color: "#666" }}>
                  cursor {ln.cursor}</td>
                <td style={cellStyle}>
                  <button type="button" title={help.step} disabled={off}
                          style={{ ...chipStyle(false), opacity: off ? 0.4 : 1,
                                   cursor: off ? "default" : "pointer" }}
                          onClick={() => post("/step", { lane: laneName })}>step</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <h3 style={{margin: "1.5rem 0 0.5rem", color: "#888", fontSize: "0.9rem" }}>
        Registry <span style={{ ...monoStyle, color: "#666" }}>
          ({rows.length} handle{rows.length === 1 ? "" : "s"})</span></h3>
      {rows.length === 0 ? (
        <div style={{ ...monoStyle, color: "#666" }}>empty</div>
      ) : (
        <table style={{ ...tableStyle, width: "auto" }}>
          <thead>
            <tr>{["name", "kind", "owner", "ptr", "note"].map((h) => (
              <th key={h} style={{ ...cellStyle, ...monoStyle, color: "#888" }}>{h}</th>))}</tr>
          </thead>
          <tbody>
            {rows.map(([name, h]) => (
              <tr key={name}>
                <td style={{ ...cellStyle, ...monoStyle, color: kOk }}>{name}</td>
                <td style={{ ...cellStyle, ...monoStyle, color: "#888" }}>{h.kind}</td>
                <td style={{ ...cellStyle, ...monoStyle, color: "#888" }}>
                  {h.owner ? (nameOf[h.owner] ?? h.owner) : "\u2014"}</td>
                <td style={{ ...cellStyle, ...monoStyle, color: "#666" }}>{h.ptr}</td>
                <td style={{ ...cellStyle, ...monoStyle, color: "#666" }}>{h.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}