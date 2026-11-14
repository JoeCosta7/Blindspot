import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The backend runs on :8000 (see CLAUDE.md). Everything under /api is proxied
// so the browser never needs CORS in development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        // Keep NDJSON progress / scout streams unbuffered so the UI can update live.
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => {
            proxyReq.setHeader("Accept-Encoding", "identity");
          });
        },
      },
    },
  },
});
