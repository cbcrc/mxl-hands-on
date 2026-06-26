import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/domains": "http://localhost:9600",
      "/files":   "http://localhost:9600",
      "/pipeline":"http://localhost:9600",
    },
  },
});
