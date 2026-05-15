import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/get-domains":  "http://localhost:9660",
      "/domains":      "http://localhost:9660",
      "/scan-domain":  "http://localhost:9660",
      "/flow-info":    "http://localhost:9660",
    },
  },
});
