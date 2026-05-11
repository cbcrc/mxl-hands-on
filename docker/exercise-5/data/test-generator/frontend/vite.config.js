import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/status":             "http://localhost:9610",
      "/patterns":           "http://localhost:9610",
      "/video-test-pattern": "http://localhost:9610",
      "/audio-test-pattern": "http://localhost:9610",
      "/timecode":           "http://localhost:9610",
      "/ident":              "http://localhost:9610",
    },
  },
});
