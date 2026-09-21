// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { useLayoutEffect, useRef } from "react";
import { sectionStyle, tableStyle, cellStyle, monoStyle, kOk, kWarn } from "./styles";

const kCols = ["seq", "lane", "index", "write OTS", "write wall", "read wall",
               "age ms", "transit ms", "write late ms"];

// Same height and stick-to-bottom rule as the console in App.jsx, for the same
// reason: a 29.97 fps scenario adds ~1800 rows a minute, and an uncapped table
// pushes the transport controls off the top of an ever-growing page.
const boxStyle = { maxHeight: "22rem", overflow: "auto" };

// The section's own background, opaque: the rows scroll *under* the header, and a
// transparent one would let them show through.
const headStyle = { ...cellStyle, ...monoStyle, color: "#888",
                    position: "sticky", top: 0, background: "#1c1c1c" };

export default function Timing({ events }) {
  // A read step that could not resolve its flow's rate carries no ots_ns (calls.cpp:381),
  // so that is the filter: these are exactly the events with something to time.
  const rows = events.filter((e) => e.ots_ns !== undefined);

  // Darwin's CLOCK_REALTIME is microsecond-granular, so every t_wall_ns there ends in
  // "000" and a finer decimal on a ms value is a permanent zero. Read from the data
  // rather than hardcoded per platform. One row can fool it once in a thousand; the
  // next event corrects it.
  const usHost = rows.length > 0 && rows.every((e) => e.t_wall_ns.endsWith("000"));
  const ms = (v) => (typeof v === "number") ? v.toFixed(usHost ? 3 : 6) : "\u2014";

  // The first event of each distinct call in a fresh process is a warm-up outlier: a
  // slow first mxlFlowReaderGetGrain stamps readNs late, inflating age and transit.
  const seen = new Set();
  const marked = rows.map((e) => {
    const first = !seen.has(e.call);
    seen.add(e.call);
    return { e, first };
  });

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
        ({rows.length} row{rows.length === 1 ? "" : "s"})</span></h2>
      {rows.length === 0 ? (
        <div style={{ ...monoStyle, color: "#666" }}>No timed reads yet.</div>
      ) : (
        <div style={boxStyle} ref={boxRef} onScroll={onScroll}>
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
        </div>
      )}
      <div style={{ ...monoStyle, color: "#666", marginTop: "0.5rem" }}>
        {"\u2020"} first of its call in this process -- warm-up, not a measurement.
        {usHost ? "  Host clock is microsecond-granular." : ""}
      </div>
    </section>
  );
}