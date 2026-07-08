// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    proxy: {
      "/get-domains":  "http://localhost:9600",
      "/domains":      "http://localhost:9600",
      "/scan-domain":  "http://localhost:9600",
      "/pipeline":     "http://localhost:9600",
      "/prompter-api": "http://localhost:9600",
      "/prompter":     "http://localhost:9600",
      "/prompter-ws":  { target: "ws://localhost:9600", ws: true },
    },
  },
});
