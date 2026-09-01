// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { sectionStyle, chipStyle, monoStyle, kOk, kBad } from "./styles";

const API = "";

const help = {
  run: "Start every lane. Each lane is its own thread with its own cursor, and there " +
       "is no barrier between them: cross-lane ordering is delay_before_ms only.",
  pause: "Stop after the current step. The step in flight finishes, its delay " +
         "included, so pause is not instant.",
  reset: "Destroy every handle and rewind every lane to step 0. Releasing a writer " +
         "can delete the flow it created, so this is not a cheap undo.",
};

export default function Transport({ state, apply }) {
  const [msg, setMsg] = useState(null);
  const running = state.running ?? false;

  // Its own copy of post(), not shared with the builder's: this one feeds the
  // response into apply() and returns nothing, that one returns ok for sequencing.
  // Two similar functions, factored if a third ever appears.
  async function post(path, body, done) {
    try {
      const res = await fetch(API + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: (body === undefined) ? undefined : JSON.stringify(body),
      });
      const doc = await res.json();
      if (res.ok) apply(doc);           // every transport verb answers with engine.state()
      setMsg({ text: res.ok ? done : (doc.error ?? ("HTTP " + res.status)), ok: res.ok});
    } catch (e) {
        setMsg({ text: String(e), ok: false});
    }
  }

  return (
    <section style={sectionStyle}>
      <h2 style={{ marginBottom: "1rem" }}>Transport</h2>
      <button type="button" style={chipStyle(running)} title={running ? help.pause : help.run}
              onClick={() => running ? post("/pause", undefined, "paused")
                                     : post("/run",  undefined, "running")}>
        {running ? "pause" : "run"}</button>
      <button type="button" style={chipStyle(false)} title={help.reset}
              onClick={() => post("/reset", undefined, "reset")}>reset</button>
      <span style={{ ...monoStyle, marginLeft: "1rem", color: running ? kOk : "#888" }}>
        {running ? "running" : "idle"}</span>
      {msg && <span style={{ ...monoStyle, marginLeft: "0.75rem",
                             color: msg.ok ? kOk : kBad }}>{msg.text}</span>}
    </section>
  );
}