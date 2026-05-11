import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/status":   "http://localhost:9620",
      "/hls-link": "http://localhost:9620",
      "/apply":    "http://localhost:9620",
      "/stop":     "http://localhost:9620",
    },
  },
});
