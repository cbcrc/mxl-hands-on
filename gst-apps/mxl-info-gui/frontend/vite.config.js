import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/get-domains":  "http://localhost:9600",
      "/domains":      "http://localhost:9600",
      "/scan-domain":  "http://localhost:9600",
      "/flow-info":    "http://localhost:9600",
      "/orphan-flows": "http://localhost:9600",
    },
  },
});
