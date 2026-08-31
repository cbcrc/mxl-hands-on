// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { useState, useEffect } from "react";
import { sectionStyle, tableStyle, cellStyle, chipStyle, monoStyle, kBad } from "./styles";

const API = "";

const inputStyle = { ...monoStyle, padding: "0.2rem" };

// Which registry kind each handle-typed param wants. store_as is deliberately absent:
// it names a handle that must NOT exist yet, so a list of existing ones is the wrong
// input for it. The catalog's type says "handle" for both; only the name separates them.
const handleKinds = { instance: "instance", writer: "flow_writer",
                      reader: "flow_reader", group: "sync_group", grain: "grain" };

const labelStyle = { ...monoStyle, color: "#888", marginRight: "1rem" };

// "out": {"grain": "g"} -- the key names the kind and is documentation only
// (engine.cpp:716); the value is the registry name every creating adapter reads as
// "store_as". A wrong key here is harmless; a wrong *value* is a handle nobody finds.
const outKinds = {
  mxlCreateInstance: "instance", mxlCreateFlowWriter: "writer",
  mxlCreateFlowReader: "reader", mxlCreateFlowSynchronizationGroup: "group",
  mxlFlowWriterOpenGrain: "grain", mxlFlowReaderGetGrain: "grain",
  mxlFlowReaderGetGrainSlice: "grain", mxlFlowReaderGetGrainNonBlocking: "grain",
  mxlFlowReaderGetGrainSliceNonBlocking: "grain",
};

// setCursor and repeat are lane pseudo-steps, deliberately absent from /abi-calls so the
// catalog stays exactly 42 and diffable against `nm -D`. The builder still has to offer
// them, so their params are spelled here in the catalog's own shape -- which keeps the
// form generator to one code path instead of a special case per pseudo-step.
const pseudoCalls = [
    { name: "setCursor", header: "(lane)",
      description: "Seed this lane's cursor. Never enters the library.",
      params: [{ name: "index", type: "object", required: true,
                 description: "{mode, edit_rate} -- or {mode: \"literal\", value: N}"}] },
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
            const type = res.headers.get("content-type") ?? "";
            if (!type.includes("application/json")) throw new Error("not JSON: " + type);
            setCalls([...pseudoCalls, ...(await res.json())]);
        } catch (e) {
            setCatalogError(String(e));
        }
      }
      load();
    }, []);
    return { calls, catalogError };
}

function useHandles(pollMs = 1000) {
  const [handles, setHandles] = useState({});
  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const res = await fetch(API + "/state");
        const body = await res.json();
        if (alive && body?.handles) setHandles(body.handles);
      } catch { /* nothing to say: the console's own poll already reports a dead backend */ }
    }
    poll();
    const id = setInterval(poll, pollMs);
    return () => { alive = false; clearInterval(id); };
  }, [pollMs]);
  return handles;
}

export default function Builder() {
    const { calls, catalogError } = useCatalog();
    const handles = useHandles();
    const [filter, setFilter] = useState("");
    const [selected, setSelected] = useState(null);
    const [args, setArgs] = useState({});
    const [step, setStep] = useState({});
    const setField = (k, v) => setStep((prev) => ({ ...prev, [k]: v }));

    // Every call has its own arguments. Without this, selecting mxlCreateFlowReader
    // after mxlCreateFlowWriter silently carries the old flow_def along.
    useEffect(() => { setArgs({}); setStep({}); }, [selected]);

    // Immutable update. React compares state by identity, so mutating `args` and
    // calling setArgs(args) would re-render nothing. Python: {**prev, name: value}.
    const setArg = (name, value) => setArgs((prev) => ({ ...prev, [name]: value}));

    // One switch, six types. Defined inside Builder so it closes over args and
    // setArg -- a nested def that sees the enclosing scope, same as Python.
    function field(p) {
      const v = args[p.name];
      if (p.type === "bool")
        return <input type="checkbox" checked={v ?? false}
                      onChange={(e) => setArg(p.name, e.target.checked)} />;
      if (p.type === "rational")
        return ["num", "den"].map((k) => (
          <input key={k} value={v?.[k] ?? ""} placeholder={k}
                 onChange={(e) => setArg(p.name, { ...v, [k]: e.target.value})}
                 style={{ ...inputStyle, width: "5rem" }} />
        ));
      if (p.name === "index") return indexField(p);
      if (p.type === "object" || p.name === "flow_def")
        return <textarea value={v ?? ""} rows={6}
                         onChange={(e) => setArg(p.name, e.target.value)}
                         style={{ ...inputStyle, width: "28rem" }} />;
      const kind = handleKinds[p.name];
      if (p.type === "handle" && kind) {
        const names = Object.keys(handles).filter((n) => handles[n].kind === kind);
        return (
          <select value={v ?? ""} onChange={(e) => setArg(p.name, e.target.value)}
                  style={{ ...inputStyle, width: "14rem" }}>
            <option value="">-- {kind} --</option>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
            {v && !names.includes(v) && <option value={v}>{v} (gone)</option>}
          </select>
        )
      }
      return <input value={v ?? ""} onChange={(e) => setArg(p.name, e.target.value)}
                    style={{ ...inputStyle, width: "14rem"}} />;
    }

    // An index expression is an object, not a scalar. edit_rate and reader appear only
    // for the modes that needs them -- and both are optional even then: resolveIndex
    // falls back to the step's own edit_rate / reader argument when the expression
    // omits them, which is why a read step never spells its reader twice.
    function indexField(p) {
      const v = args[p.name] ?? {};
      const mode = v.mode ?? "literal";
      const set = (patch) => setArg(p.name, { ...v, ...patch });
      return (
        <span>
          <select value={mode} onChange={(e) => set({ mode: e.target.value })}
                  style={{ ...inputStyle, marginRight: "0.3rem" }}>
            {["literal", "cursor", "current", "head"].map((m) =>
              <option key={m} value={m}>{m}</option>)}
          </select>
          {mode === "literal" &&
            <input value={v.value ?? ""} placeholder="value"
                   onChange={(e) => set({ value: e.target.value })}
                   style={{ ...inputStyle, width: "10rem" }} />}
          {mode === "current" && ["num", "den"].map((k) =>
            <input key={k} value={v.edit_rate?.[k] ?? ""} placeholder={k}
                   onChange={(e) => set({ edit_rate: { ...v.edit_rate, [k]: e.target.value } })}
                   style={{ ...inputStyle, width: "5rem" }} />)}
          {mode === "head" &&
            <input value={v.reader ?? ""} placeholder="reader"
                   onChange={(e) => set({ reader: e.target.value })}
                   style={{ ...inputStyle, width: "7rem" }} />}
          <input value={v.offset ?? ""} placeholder="offset"
                 onChange={(e) => set({ offset: e.target.value })}
                 style={{ ...inputStyle, width: "5rem", marginLeft: "0.3rem"}} />
        </span>);
    }

    // The form holds strings; the wire does not, and the two disagree per type.
    // uint64 goes out as a *string* on purpose -- the M9 number contract, because a
    // ns timestamp passes 2^53 and JS has only doubles; argUint64 takes either.
    // rational must be real numbers: argRational test is_number_integer().
    function buildArgs() {
      const out = {};
      for (const p of params) {
        const v = args[p.name];
        if (p.type === "bool") { if (v) out[p.name] = true; continue; }
        if (p.type === "rational") {
          if (v?.num && v?.den) out[p.name] = { num: Number(v.num), den: Number(v.den) };
          continue;
        }
        if (p.name === "index") {
          const m = v?.mode ?? "literal";
          const expr = { mode: m };
          if (m === "literal") { if (!v?.value) continue; expr.value = Number(v.value); }
          if ((m === "current") && v?.edit_rate?.num && v?.edit_rate?.den)
            expr.edit_rate = { num: Number(v.edit_rate.num), den: Number(v.edit_rate.den) };
          if ((m === "head") && v?.reader) expr.reader = v.reader;
          if (v?.offset) expr.offset = Number(v.offset);
          out[p.name] = expr;
          continue;
        }
        if (v === undefined || v === "") continue;    // empty means absent, not ""
        if (p.type === "object") {
          try { out[p.name] = JSON.parse(v); }
          catch (e) { out[p.name] = "!! " + e.message; }
          continue;
        }
        out[p.name] = v;
      }
      return out;
    }

    function buildStep() {
      const body = { call: call.name, args: buildArgs() };
      if (step.id) body.id = step.id;
      if (stores && step.out) body.out = { [outKinds[call.name] ?? "handle"]: step.out};
      if (step.note) body.note = step.note;
      if (fills) {
        body.fill = { mode: step.fillMode ?? "none"};
        if (body.fill.mode === "const") body.fill.byte = Number(step.fillByte ?? 0);
        if (step.stamp) body.fill.stamp = true;
      }
      if ((timing === "delay") && step.delay) body.delay_before_ms = Number(step.delay);
      if (timing === "pace") {
        body.pace = {};
        if (step.offset_ms) body.pace.offset_ms = Number(step.offset_ms);
        if (step.jitter_ms) body.pace.jitter_ms = Number(step.jitter_ms);
        }
      if (step.advance) body.advance_cursor = Number(step.advance);
      return body;
    }

    const shown = calls.filter((c) => c.name.toLowerCase().includes(filter.toLowerCase()));
    const call = calls.find((c) => c.name === selected) ?? null;

    // store_as and fill are catalog params but not step args: fill inside args is read
    // as an index expression by resolveArgs, and store_as is spelled "out" in a step.
    const params = (call?.params ?? []).filter((p) => (p.name !== "store_as") && (p.name !== "fill"));
    const stores = (call?.params ?? []).some((p) => p.name === "store_as");
    const fills  = (call?.params ?? []).some((p) => p.name === "fill");
    const timing = step.timing ?? "none"; 

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
                    <th style={cellStyle}>req</th><th style={cellStyle}>description</th>
                    <th style={cellStyle}>value</th></tr>
              </thead>
              <tbody>
                {params.map((p) => (
                  <tr key={p.name}>
                    <td style={{ ...cellStyle, ...monoStyle }}>{p.name}</td>
                    <td style={{ ...cellStyle, ...monoStyle }}>{p.type}</td>
                    <td style={cellStyle}>{p.required ? "yes" : ""}</td>
                    <td style={cellStyle}>{p.description}</td>
                    <td style={cellStyle}>{field(p)}</td>
                  </tr>
                ))}
                {params.length === 0 && (
                  <tr><td style={cellStyle} colSpan={5}>no arguments</td></tr>
                )}
              </tbody>
            </table>
            <div style={{ marginTop: "0.75rem" }}>
              <label style={labelStyle}>id{" "}
                <input value={step.id ?? ""} onChange={(e) => setField("id", e.target.value)}
                       style={{ ...inputStyle, width: "6rem" }} /></label>
              {stores && (
                <label style={labelStyle}>out ({outKinds[call.name] ?? "handle"}){" "}
                <input value={step.out ?? ""} onChange={(e) => setField("out", e.target.value)}
                       style={{ ...inputStyle, width: "6rem" }} /></label>)}
              <label style={labelStyle}>note{" "}
                <input value={step.note ?? ""} onChange={(e) => setField("note", e.target.value)}
                       style={{ ...inputStyle, width: "24rem" }} /></label>
              {fills && (<>
                <label style={labelStyle}>fill{" "}
                  <select value={step.fillMode ?? "none"} style={inputStyle}
                          onChange={(e) => setField("fillMode", e.target.value)}>
                    {["none", "const", "ramp"].map((m) => <option key={m} value={m}>{m}</option>)}
                  </select></label>
                  {step.fillMode === "const" &&
                    <label style={labelStyle}>byte{" "}
                      <input value={step.fillByte ?? ""} style={{ ...inputStyle, width: "4rem" }}
                             onChange={(e) => setField("fillByte", e.target.value)} /></label>}
                  <label style={labelStyle}>stamp{" "}
                    <input type="checkbox" checked={step.stamp ?? false}
                           onChange={(e) => setField("stamp", e.target.checked)} /></label>
              </>)}
            </div>
            <div style={{ marginTop: "0.5rem" }}>
              <label style={labelStyle}>timing{" "}
                <select value={timing} style={inputStyle}
                        onChange={(e) => setField("timing", e.target.value)}>
                  {["none", "delay", "pace"].map((t) => <option key={t} value={t}>{t}</option>)}
                </select></label>
              {timing === "delay" &&
                <label style={labelStyle}>delay_before_ms{" "}
                  <input value={step.delay ?? ""} style={{ ...inputStyle, width: "6rem" }}
                         onChange={(e) => setField("delay", e.target.value)} /></label>}
              {timing === "pace" && ["offset_ms", "jitter_ms"].map((k) =>
                <label key={k} style={labelStyle}>{k}{" "}
                  <input value={step[k] ?? ""} style={{ ...inputStyle, width: "5rem" }}
                         onChange={(e) => setField(k, e.target.value)} /></label>)}
              <label style={labelStyle}>advance_cursor{" "}
                <input value={step.advance ?? ""} style={{ ...inputStyle, width: "5rem" }}
                       onChange={(e) => setField("advance", e.target.value)} /></label>
            </div>
            <pre style={{ ...monoStyle, background: "#111", padding: "0.75rem",
                          borderRadius: "4px", overflowX: "auto" }}>
              {JSON.stringify(buildStep(), null, 2)}
            </pre>    
          </>
        )}
      </section>
    );
}