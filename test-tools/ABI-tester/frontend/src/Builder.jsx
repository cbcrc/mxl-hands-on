// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { useState, useEffect, } from "react";
import { sectionStyle, tableStyle, cellStyle, chipStyle, monoStyle, kOk, kBad } from "./styles";

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

// The args table gets its description from /abi-calls. Step-level fields appear in no
// catalog, so their documentation lives here -- keyed by the wire name, so the tooltip
// and the JSON key can never disagree.
const stepHelp = {
  id: "Step name. Used in the engine's error messages and as the target of `repeat1. " +
      "Defaults to the lane letter plus the next free ordinal.",
  out: "Registry name for the handle this step creates. The engine passes it to the " +
       "adapter as store_as; the key is documentation only.",
  note: "A comment for whoever reads the scenario. The engine never reads it.",
  fill: "How the payload is written before commit. `none` leaves whatever the previous " +
        "occupant of that ring slot left behind.",
  byte: "0-255, memset over the whole grain. ~180 us for a 5.5 MB v210 grain @1920x1080p29.97",
  stamp: "Write the 24-byte magic/index/write-time stamp at offset 0, after the fill -- " +
         "a memset would erase it. It is what makes true propagation delay measurable.",
  timing: "When this step runs. `delay` waits N ms from the previous step; `pace` aims at " +
          "the grain's own OTS, so a late step catches up instead of pushing the rest.",
  delay_before_ms: "Relative wait before the step. Its clock restarts each step, so slop " +
                   "compounds down the lane -- 115-367 us per step, measured.",
  offset_ms: "Shift the pace target. Staggering it across lanes is what took 96 lanes " +
             "from 1582 us to 182 us: phase sets p99, not average load.",
  jitter_ms: "Random +/- added to the pace target, drawn per lane.",
  advance_cursor: "Grains (or samples) added to the lane cursor after this step. " +
                  "Usually 1, on the commit: a grain costs two steps -- open then commit --" +
                  "and advances the cursor once, so putting it on both double-advances.",
  lane: "Which lane thread queues this step. Lanes share one registry but have no barrier " +
        "between them; cross-lane ordering is a delay_before_ms today.",
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

const laneName = (n) => { let s = ""; for (let i = n; i >= 0; i = Math.floor(i / 26) - 1)
                            s = String.fromCharCode(65 + (i % 26)) + s; return s; };

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

function useServerState(pollMs = 1000) {
  const [state, setState] = useState({});
  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const res = await fetch(API + "/state");
        const body = await res.json();
        if (alive && body?.handles) setState(body);    // the whole body, not just handles
      } catch { /* nothing to say: the console's own poll already reports a dead backend */ }
    }
    poll();
    const id = setInterval(poll, pollMs);
    return () => { alive = false; clearInterval(id); };
  }, [pollMs]);
  return state;
}

// One fetch, two callers: the mount effect and (in 14-4d-ii) the save button.
// State-free and at module scope, so each caller owns its own setState -- the
// effect needs an `alive` guard against a component unmounted mid-flight, the
// button does not.
async function fetchScenarioNames() {
  const res = await fetch(API + "/scenarios");
  if (!res.ok) throw new Error("HTTP " + res.status);
  return await res.json();  
}

// The list changes only when we save, so it is fetched once and re-fetched by
// hand -- no poll.
function useScenarioList() {
  const [names, setNames] = useState([]);
  const reload = async () => {
    try { setNames(await fetchScenarioNames()); }
    catch { /* the console's own poll already reports a dead backend */ }
  };
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const list = await fetchScenarioNames();
        if (alive) setNames(list);
      } catch { /* same */ }
    }
    load();
    return () => { alive = false; };
  }, []);
  return { names, reload };
}

export default function Builder() {
    const { calls, catalogError } = useCatalog();
    const state = useServerState();
    const handles = state.handles ?? {};
    const [filter, setFilter] = useState("");
    const [selected, setSelected] = useState(null);
    const [args, setArgs] = useState({});
    const [step, setStep] = useState({});
    const [lane, setLane] = useState("A");
    const [lanes, setLanes] = useState({}); // {A: [step, ...]}, the scenario being built
    const setField = (k, v) => setStep((prev) => ({ ...prev, [k]: v }));
    const addStep = () =>
      setLanes((prev) => ({ ...prev, [lane]: [ ...(prev[lane] ?? []), buildStep()] }));
    const removeStep = (L, i) =>
      setLanes((prev) => ({ ...prev, [L]: prev[L].filter((_, n) => n !== i) }));
    const moveStep = (L, i, d) => setLanes((prev) => {
      const j = i + d;
      if ((j < 0) || (j >= prev[L].length)) return prev;    // same object: React bails out
      const list = [...prev[L]];
      [list[i], list[j]] = [list[j], list[i]];
      return { ...prev, [L]: list };
    });
    const [description, setDescription] = useState("");
    const { names: scenarioNames, reload: reloadScenarios } = useScenarioList();
    const [scenarioName, setScenarioName] = useState("");

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
        const live = Object.keys(handles).filter((n) => handles[n].kind === kind);
        // Handles an earlier step in this lane promises but has not created yet. The
        // registry is runtime state; a scenario is authored against the future.
        // outKinds speaks the same vocabulary as the input param names, which is what
        // makes this one comparison rather than a second map.
        const promised = (lanes[lane] ?? [])
          .filter((s) => (outKinds[s.call] === p.name) && s.out)
          .map((s) => Object.values(s.out)[0])
          .filter((n) => !live.includes(n));
        const names = [...live, ...promised];
        return (
          <select value={v ?? ""} onChange={(e) => setArg(p.name, e.target.value)}
                  style={{ ...inputStyle, width: "14rem" }}>
            <option value="">-- {kind} --</option>
            {live.map((n) => <option key={n} value={n}>{n}</option>)}
            {promised.map((n) => <option key={n} value={n}>{n} (pending)</option>)}
            {v && !names.includes(v) && <option value={v}>{v} (gone)</option>}
          </select>
        )
      }
      if (p.name === "domain") {
        const aliases = Object.keys(state.domains ?? {});
        return (
          <select value={v ?? ""} onChange={(e) => setArg(p.name, e.target.value)}
                  style={{ ...inputStyle, width: "14rem"}}>
            <option value="">-- default --</option>
            {aliases.map((a) =>
              <option key={a} value={a} title={state.domains[a]}>{a}</option>)}
          </select>)
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
      body.id = step.id || nextId(lane);
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

    // a0, a1, ... -- the bundled scenarios' own convention. The next *free* ordinal,
    // not the count: deleting a middle step would otherwise reissue an id still in use,
    // and `repeat` resolves its target by id with no duplicate check in loadScenario.
    function nextId(L) {
      const used = new Set((lanes[L] ?? []).map((s) => s.id));
      for (let n = 0; ; n++) {
        const id = L.toLowerCase() + n;
        if (!used.has(id)) return id;
      }
    }

    // Empty lanes are dropped: loadScenario empties every pool lane the document does
    // not name (engine.cpp:889), so an explicit "C": [] and an absent C mean the same
    // thing to the engine -- but the file reads better without it.
    function buildScenario() {
      const doc = {};
      if (scenarioName) doc.name = scenarioName;
      if (description) doc.description = description;
      doc.lanes = {};
      for (const [L, steps] of Object.entries(lanes)) {
        if (steps.length > 0) doc.lanes[L] = steps;
      }
      return doc;
    }

    const [msg, setMsg] = useState(null);     // {text, ok}

    // Every transport button goes through here. Post /scenario answers 200 with the
    // engine state and 400 with {ok, error}, so res.ok is the verdict and doc.error is
    // the message -- there is no "ok" field on the success path to test.
    async function post(path, body, done) {
      try {
        const res = await fetch(API + path, {
          method: "POST",
          headers: { "Content-Type": "application/json"},
          body: (body === undefined) ? undefined : JSON.stringify(body),
        });
        const doc = await res.json();
        setMsg({ text: res.ok ? done : (doc.error ?? ("HTTP " + res.status)), ok: res.ok});
        return res.ok;
      } catch (e) {
          setMsg({ text: String(e), ok: false });
          return false;
      }
      
    }

    async function openScenario(name) {
      if (!name) return;
      try {
        const res = await fetch(API + "/scenarios/" + encodeURIComponent(name));
        const doc = await res.json();
        if (!res.ok) {
          setMsg({ text: doc.error ?? ("HTTP " + res.status), ok: false});
          return;
        }
        setLanes(doc.lanes ?? {});
        setDescription(doc.description ?? "");
        setScenarioName(doc.name ?? name);
        setMsg({ text: `opened ${name} -- not loaded into the engine`, ok: true});
      } catch (e) {
        setMsg({ text: String(e), ok: false});
      }
    }

    // Overwriting is a real loss -- the scenarios dir is the only copy of anything
    // built here that has not been committed -- so an existing name is confirmed
    // and the button says so before you press it.
    async function saveScenario() {
      if (!scenarioName) { setMsg({ text: "name the scenario first", ok: false }); return; }
      if (scenarioNames.includes(scenarioName) &&
          !window.confirm(`Overwrite ${scenarioName}.json?`)) return;
      const ok = await post("/scenarios/" + encodeURIComponent(scenarioName),
                            buildScenario(), `saved as ${scenarioName}.json`);
      if (ok) reloadScenarios();
    }

    async function loadIntoEngine() {
      const ok = await post("/scenario", buildScenario(), "loaded");
      if (!ok) return;
      // POST /scenario clears _running but NOT the registry. M13.5: running a second
      // time with handles still in place refuses the create steps as "name already in
      // use" and runs the rest on the old ones -- 87 % green and not a measurement.
      const held = Object.keys(handles).length;
      if (held > 0) setMsg({ text: `loaded -- ${held} handle(s) still in the registry; `
                                 + `reset before running`, ok: false});
    }

    const resetEngine = () => post("/reset", undefined, "reset");

    const shown = calls.filter((c) => c.name.toLowerCase().includes(filter.toLowerCase()));
    const call = calls.find((c) => c.name === selected) ?? null;

    // store_as and fill are catalog params but not step args: fill inside args is read
    // as an index expression by resolveArgs, and store_as is spelled "out" in a step.
    const params = (call?.params ?? []).filter((p) => (p.name !== "store_as") && (p.name !== "fill"));
    const stores = (call?.params ?? []).some((p) => p.name === "store_as");
    const fills  = (call?.params ?? []).some((p) => p.name === "fill");
    // pace needs a flow handle to read a grain rate from *and* a resolved index to aim
    // at (engine.cpp:1062-1077). On any other call it loads fine and fails mid-run, so
    // it is not offered. delay_before_ms and advance_cursor apply to every step
    const names  = (call?.params ?? []).map((p) => p.name);
    const pacable = names.includes("index") &&
                    (names.includes("writer") || names.includes("reader"));
    const timing = step.timing ?? "none"; 

    return (
      <section style={sectionStyle}>
        <h2 style={{ marginBottom: "1rem" }}>Builder</h2>
        {catalogError && <div style={{ color: kBad}}>{catalogError}</div>}
        <input value={filter} onChange={(e) => setFilter(e.target.value)}
               placeholder={`filter ${calls.length} calls`}
               style={{ ...monoStyle, marginBottom: "0.5rem", padding: "0.3rem", width: "16rem" }} />
        <div style={{ marginBottom: "0.75rem" }}>
          {/* Every call has its own arguments. Without the reset, selecting
              mxlCreateFlowReader after mxlCreateFlowWriter silently carries the
              old flow_def along. Done here rather than in an effect on [selected]:
              an effect renders once with the previous call's from state first. */}
          {shown.map((c) => (
            <button type="button" key={c.name} style={chipStyle(c.name === selected)}
                    onClick={() => { setSelected(c.name); setArgs({}); setStep({}); }}>
              {c.name}</button>
          ))}    
        </div>
        {call && (
          <>
            <div style={{ ...monoStyle, color: "#888", marginBottom: "0.5rem" }}>
              {call.header} - {call.description}
            </div>
            <div style={{ overflowX: "auto" }}>
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
            </div>
            <div style={{ marginTop: "0.75rem" }}>
              <label style={labelStyle} title={stepHelp.id}>id{" "}
                <input value={step.id ?? ""} onChange={(e) => setField("id", e.target.value)}
                       placeholder={nextId(lane)}
                       style={{ ...inputStyle, width: "6rem" }} /></label>
              {stores && (
                <label style={labelStyle} title={stepHelp.out}>out ({outKinds[call.name] ?? "handle"}){" "}
                <input value={step.out ?? ""} onChange={(e) => setField("out", e.target.value)}
                       style={{ ...inputStyle, width: "6rem" }} /></label>)}
              <label style={labelStyle} title={stepHelp.note}>note{" "}
                <input value={step.note ?? ""} onChange={(e) => setField("note", e.target.value)}
                       style={{ ...inputStyle, width: "24rem" }} /></label>
              {fills && (<>
                <label style={labelStyle} title={stepHelp.fill}>fill{" "}
                  <select value={step.fillMode ?? "none"} style={inputStyle}
                          onChange={(e) => setField("fillMode", e.target.value)}>
                    {["none", "const", "ramp"].map((m) => <option key={m} value={m}>{m}</option>)}
                  </select></label>
                  {step.fillMode === "const" &&
                    <label style={labelStyle} title={stepHelp.byte}>byte{" "}
                      <input value={step.fillByte ?? ""} style={{ ...inputStyle, width: "4rem" }}
                             onChange={(e) => setField("fillByte", e.target.value)} /></label>}
                  <label style={labelStyle} title={stepHelp.stamp}>stamp{" "}
                    <input type="checkbox" checked={step.stamp ?? false}
                           onChange={(e) => setField("stamp", e.target.checked)} /></label>
              </>)}
            </div>
            <div style={{ marginTop: "0.5rem" }}>
              <label style={labelStyle} title={stepHelp.timing}>timing{" "}
                <select value={timing} style={inputStyle}
                        onChange={(e) => setField("timing", e.target.value)}>
                  {(pacable ? ["none", "delay", "pace"] : ["none", "delay"])
                    .map((t) => <option key={t} value={t}>{t}</option>)}
                </select></label>
              {timing === "delay" &&
                <label style={labelStyle} title={stepHelp.delay_before_ms}>delay_before_ms{" "}
                  <input value={step.delay ?? ""} style={{ ...inputStyle, width: "6rem" }}
                         onChange={(e) => setField("delay", e.target.value)} /></label>}
              {timing === "pace" && ["offset_ms", "jitter_ms"].map((k) =>
                <label key={k} style={labelStyle} title={stepHelp[k]}>{k}{" "}
                  <input value={step[k] ?? ""} style={{ ...inputStyle, width: "5rem" }}
                         onChange={(e) => setField(k, e.target.value)} /></label>)}
              <label style={labelStyle} title={stepHelp.advance_cursor}>advance_cursor{" "}
                <input value={step.advance ?? ""} style={{ ...inputStyle, width: "5rem" }}
                       onChange={(e) => setField("advance", e.target.value)} /></label>
            </div>
            <div style={{ marginTop: "0.5rem" }}>
              <label style={labelStyle} title={stepHelp.lane}>lane{" "}
                <select value={lane} onChange={(e) => setLane(e.target.value)} style={inputStyle}>
                  {Array.from({ length: state.lane_pool ?? 1 }, (_, i) => laneName(i))
                        .map((L) => <option key={L} value={L}>{L}</option>)}
                </select></label>
              <button type="button" onClick={addStep} style={chipStyle(true)}>add step</button>
            </div>
            <pre style={{ ...monoStyle, background: "#111", padding: "0.75rem",
                          borderRadius: "4px", overflowX: "auto" }}>
              {JSON.stringify(buildStep(), null, 2)}
            </pre>    
          </>
        )}
        <div style={{ marginTop: "1rem"}}>
          {Object.keys(lanes).map((L) => (
            <div key={L} style={{ marginBottom: "0.5rem" }}>
              <div style={{ ...monoStyle, color: "#888" }}>lane {L} - {lanes[L].length} steps</div>
              {lanes[L].map((s, i) => (
                <div key={i} style={{ ...monoStyle, padding: "0.15rem 0" }}>
                  <button type="button" style={chipStyle(false)}
                          onClick={() => removeStep(L, i)}>x</button>
                  <button type="button" style={chipStyle(false)}
                          onClick={() => moveStep(L, i, -1)}>{"\u25b2"}</button>
                  <button type="button" style={chipStyle(false)}
                          onClick={() => moveStep(L, i, +1)}>{"\u25bc"}</button>
                  {" "}{s.id || i}{" "}{s.call}
                </div>
              ))}
            </div>
          ))}
        </div>
        <div style={{ marginTop: "1rem" }}>
          <label style={labelStyle}
                 title="Read a file from the scenarios directory into the builder. It does not load it into the engine.">
            open{" "}
            <select value="" style={inputStyle}
                    onChange={(e) => openScenario(e.target.value)}>
              <option value="">-- {scenarioNames.length} on disk --</option>
              {scenarioNames.map((n) => <option key={n} value={n}>{n}</option>)}
            </select></label>
            <label style={labelStyle}
                   title="Saved as <name>.json in the scenarios directory. Letters, digits, '-' and '_' only; the extension is added for you.">
              name{" "}
              <input value={scenarioName} onChange={(e) => setScenarioName(e.target.value)}
                     style={{ ...inputStyle, width: "14rem" }} /></label>
              <button type="button" style={chipStyle(true)} onClick={saveScenario}>
                {scenarioNames.includes(scenarioName) ? "overwrite" : "save"}</button>
        </div>
        <div style={{ marginTop: "1rem" }}>
          <label style={labelStyle} title="Free text. The engine stores it and hands it back, but never reads it.">
            description{" "}
            <input value={description} onChange={(e) => setDescription(e.target.value)}
                   style={{ ...inputStyle, width: "40rem" }} /></label>
            <button type="button" style={chipStyle(true)} onClick={loadIntoEngine}>load</button>
            <button type="button" style={chipStyle(false)} onClick={resetEngine}>reset</button>
            {msg && <span style={{ ...monoStyle, marginLeft: "0.5rem",
                                   color: msg.ok ? kOk : kBad }}>{msg.text}</span>}
          <pre style={{ ...monoStyle, background: "#111", padding: "0.75rem",
                        borderRadius: "4px", overflowX: "auto", maxHeight: "24rem" }}>
            {JSON.stringify(buildScenario(), null, 2)}
          </pre>
        </div>
      </section>
    );
}