// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { useState, useEffect } from "react";
import { sectionStyle, tableStyle, cellStyle, chipStyle, monoStyle, kBad } from "./styles";

const API = "";

// setCursor and repeat are lane pseudo-steps, deliberately absent from /abi-calls so the
// catalog stays exactly 42 and diffable against `nm -D`. The builder still has to offer
// them, so their params are spelled here in the catalog's own shape -- which keeps the
// form generator to one code path instead of a special case per pseudo-step.
const pseudoCalls = [
    { name: "setCursor", header: "(lane)",
      description: "Seed this lane's cursor. Never enters the library.",
      params: [{ name: "index", type: "object", required: true,
                 description: "{mode, edit_rate} -- or {mode: \"literal\", index: N}"}] },
    { name: "repeat", header: "(lane)",
      description: "Jump back to an earlier step, N more times. Backward only. Logs nothing.",
      params: [{ name: "to", type: "string", required: true, description: "id of an earlier step" },
               { name: "times", type: "uint64", required: true, description: "additional passes" }] },
];

function useCatalog() {
    const [calls, setCalls] = useState([]);
    const [catalogError, setCatalogError] = useState(null);
    useEffect(() => {
      async function load() {
        try {
            const res = await fetch(API + "/abi-calls");
            if (!res.ok) throw new Error("HTTP " + res.status);
            setCalls([...pseudoCalls, ...(await res.json())]);
        } catch (e) {
            setCatalogError(String(e));
        }
      }
      load();
    }, []);
    return { calls, catalogError };
}

export default function Builder() {
    const { calls, catalogError } = useCatalog();
    const [filter, setFilter] = useState("");
    const [selected, setSelected] = useState(null);

    const shown = calls.filter((c) => c.name.toLowerCase().includes(filter.toLowerCase()));
    const call = calls.find((c) => c.name === selected) ?? null;

    return (
      <section style={sectionStyle}>
        <h2 style={{ marginBottom: "1rem" }}>Builder</h2>
        {catalogError && <div style={{ color: kBad}}>{catalogError}</div>}
        <input value={filter} onChange={(e) => setFilter(e.target.value)}
               placeholder={`filter ${calls.length} calls`}
               style={{ ...monoStyle, marginBottom: "0.5rem", padding: "0.3rem", width: "16rem" }} />
        <div style={{ marginBottom: "0.75rem" }}>
          {shown.map((c) => (
            <button type="button" key={c.name} style={chipStyle(c.name === selected)}
                    onClick={() => setSelected(c.name)}>{c.name}</button>
          ))}    
        </div>
        {call && (
          <>
            <div style={{ ...monoStyle, color: "#888", marginBottom: "0.5rem" }}>
              {call.header} - {call.description}
            </div>
            <table style={tableStyle}>
              <thead>
                <tr><th style={cellStyle}>param</th><th style={cellStyle}>type</th>
                    <th style={cellStyle}>req</th><th style={cellStyle}>description</th></tr>
              </thead>
              <tbody>
                {call.params.map((p) => (
                  <tr key={p.name}>
                    <td style={{ ...cellStyle, ...monoStyle }}>{p.name}</td>
                    <td style={{ ...cellStyle, ...monoStyle }}>{p.type}</td>
                    <td style={cellStyle}>{p.required ? "yes" : ""}</td>
                    <td style={cellStyle}>{p.description}</td>
                  </tr>
                ))}
                {call.params.length === 0 && (
                  <tr><td style={cellStyle} colSpan={4}>no arguments</td></tr>
                )}
              </tbody>
            </table>
          </>
        )}
      </section>
    );
}