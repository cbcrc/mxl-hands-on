// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/domains":  "http://localhost:9600",
      "/pipeline": "http://localhost:9600",
      "/hls":      "http://localhost:9600",
    },
  },
});
