import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    proxy: {
      "/get-domains": "http://localhost:9600",
      "/domains":     "http://localhost:9600",
      "/scan-domain": "http://localhost:9600",
      "/pipeline":    "http://localhost:9600",
    },
  },
});
