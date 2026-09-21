// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { useLayoutEffect, useRef } from "react";
import { sectionStyle, tableStyle, cellStyle, monoStyle, kOk, kWarn } from "./styles";

const kCols = ["seq", "lane", "index", "write OTS", "write wall", "read wall",
               "age ms", "transit ms", "write late ms"];

// Same height and stick-to-bottom rule as the console in App.jsx, for the same
// reason: a 29.97 fps scenario adds ~1800 rows a minute, and an uncapped table
// pushes the transport controls off the top of an ever-growing page.
//
// height, not maxHeight: the empty state renders inside this same box, so the
// section is one constant height from the first render. A section that changes
// height re-clamps the *page* scroll under it, which is what made scrolling to
// the bottom during a run bounce back up.
// overflowAnchor none: this box re-pins itself to the bottom four times a second,
// and the browser's scroll anchoring treats a row moving under that as content
// shifting -- then compensates by scrolling the *page*, which is what dragged the
// main scrollbar back up off the bottom. Nothing in this app scrolls the page.
const boxStyle = { height: "22rem", overflow: "auto", overflowAnchor: "none" };

// Only the newest rows go in the DOM. The box shows about a dozen at a time, and
// rendering every row the console tail happens to hold cost ~1700 rows x 9 cells
// every 250 ms -- enough to stall the poll until the backend trimmed past its
// cursor, at which point the resync emptied the table and the section collapsed.
// The NDJSON log is where the full history lives; see mxl-transit-stats.py.
const kMaxRows = 300;

// The section's own background, opaque: the rows scroll *under* the header, and a
// transparent one would let them show through.
const headStyle = { ...cellStyle, ...monoStyle, color: "#888",
                    position: "sticky", top: 0, background: "#1c1c1c" };

export default function Timing({ events }) {
  // A read step that could not resolve its flow's rate carries no ots_ns (calls.cpp:381),
  // so that is the filter: these are exactly the events with something to time.
  const timed = events.filter((e) => e.ots_ns !== undefined);
  const rows = timed.slice(-kMaxRows);

  // Darwin's CLOCK_REALTIME is microsecond-granular, so every t_wall_ns there ends in
  // "000" and a finer decimal on a ms value is a permanent zero. Read from the data
  // rather than hardcoded per platform. One row can fool it once in a thousand; the
  // next event corrects it.
  const usHost = rows.length > 0 && rows.every((e) => e.t_wall_ns.endsWith("000"));
  const ms = (v) => (typeof v === "number") ? v.toFixed(usHost ? 3 : 6) : "\u2014";

  // The first event of each distinct call in a fresh process is a warm-up outlier: a
  // slow first mxlFlowReaderGetGrain stamps readNs late, inflating age and transit.
  // Over `timed`, not `rows`: judged against everything still held, or the oldest
  // row left in the window would wear the dagger every time one scrolled out.
  const firstSeqs = new Set();
  const seen = new Set();
  for (const e of timed) {
    if (!seen.has(e.call)) { seen.add(e.call); firstSeqs.add(e.seq); }
  }
  const marked = rows.map((e) => ({ e, first: firstSeqs.has(e.seq) }));

  // Follow the tail only while the operator is already at it. Scrolling up to read a
  // row is a deliberate act, and yanking it back every 250 ms would undo it.
  const boxRef = useRef(null);
  const stuckRef = useRef(true);

  function onScroll() {
    const el = boxRef.current;
    stuckRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  }

  useLayoutEffect(() => {
    const el = boxRef.current;
    if (el && stuckRef.current) el.scrollTop = el.scrollHeight;
  }, [rows.length]);

  return (
    <section style={sectionStyle}>
      <h2 style={{ marginBottom: "1rem" }}>Timing <span style={{ ...monoStyle, color: "#666" }}>
        ({rows.length} row{rows.length === 1 ? "" : "s"}
        {(timed.length > rows.length) ? " of " + timed.length + " held" : ""})</span></h2>
      <div style={boxStyle} ref={boxRef} onScroll={onScroll}>
        {rows.length === 0 ? (
          <div style={{ ...monoStyle, color: "#666" }}>No timed reads yet.</div>
        ) : (
          <table style={tableStyle}>
            <thead><tr>{kCols.map((c) => (
              <th key={c} style={headStyle}>{c}</th>))}</tr>
            </thead>
            <tbody>
              {marked.map(({ e, first }) => (
                <tr key={e.seq}>
                  <td style={{ ...cellStyle, ...monoStyle, color: first ? kWarn : "#888" }}>
                    {e.seq}{first ? " \u2020" : ""}</td>
                  <td style={{ ...cellStyle, ...monoStyle, color: "#888" }}>{e.lane ?? "-"}</td>
                  <td style={{ ...cellStyle, ...monoStyle, color: "#888" }}>{e.index}</td>
                  <td style={{ ...cellStyle, ...monoStyle, color: "#666" }}>{e.ots_ns}</td>
                  <td style={{ ...cellStyle, ...monoStyle, color: "#666" }}>
                    {e.stamp?.valid ? e.stamp.write_ns : "\u2014"}</td>
                  <td style={{ ...cellStyle, ...monoStyle, color: "#666" }}>{e.t_wall_ns}</td>
                  <td style={{ ...cellStyle, ...monoStyle, color: (e.age_ms < 0) ? kWarn : kOk }}>
                    {ms(e.age_ms)}</td>
                  <td style={{ ...cellStyle, ...monoStyle, color: kOk }}>
                    {ms(e.stamp?.valid ? e.stamp.transit_ms : undefined)}</td>
                  <td style={{ ...cellStyle, ...monoStyle, color: kOk }}>
                    {ms(e.stamp?.valid ? e.age_ms - e.stamp.transit_ms : undefined)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div style={{ ...monoStyle, color: "#666", marginTop: "0.5rem" }}>
        {"\u2020"} first of its call in this process -- warm-up, not a measurement.
        {usHost ? "  Host clock is microsecond-granular." : ""}
      </div>
    </section>
  );
}