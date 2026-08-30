// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0

const sectionStyle = {
  background: "#1c1c1c",
  borderRadius: "8px",
  padding: "1.5rem",
  marginBottom: "1rem",
};

const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" };

const cellStyle = { textAlign: "left", padding: "0.5rem", borderBottom: "1px solid #333" };

// A function, not a constant: the selected chip differs only by color, and two
// near-identical style objects would drift the moment one padding changes.
const chipStyle = (on) => ({
  ...monoStyle,
  background: on ? "#333" : "transparent",
  color: on ? "#eee" : "#888",
  border: "1px solid #333",
  borderRadius: "4px",
  padding: "0.15rem 0.5rem",
  marginRight: "0.35rem",
  cursor: "pointer",
});

const kOk = "#81c784", kWarn = "#ffb74d", kBad = "#e57373";

const monoStyle = { fontFamily: "ui-monospace, Menlo, monospace", fontSize: "0.8rem" };

// Export all of them

export { sectionStyle, tableStyle, cellStyle, chipStyle, kOk, kWarn, kBad, monoStyle}