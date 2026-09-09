// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/health":    "http://localhost:9600",
      "/domains":   "http://localhost:9600",
      "/abi-calls": "http://localhost:9600",
      "/scenarios": "http://localhost:9600",
      "/scenario":  "http://localhost:9600",
      "/state":     "http://localhost:9600",
      "/log":       "http://localhost:9600",
      "/call":      "http://localhost:9600",
      "/step":      "http://localhost:9600",
      "/run":       "http://localhost:9600",
      "/pause":     "http://localhost:9600",
      "/reset":     "http://localhost:9600",
    },
  },
});