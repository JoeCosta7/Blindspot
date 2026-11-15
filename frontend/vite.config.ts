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
          // Vite 8's ProxyServer type omits the event methods the proxy has at runtime.
          type ProxyRequest = { setHeader(name: string, value: string): void };
          const events = proxy as unknown as {
            on(event: "proxyReq", listener: (proxyReq: ProxyRequest) => void): void;
          };
          events.on("proxyReq", (proxyReq) => {
            proxyReq.setHeader("Accept-Encoding", "identity");
          });
        },
      },
    },
  },
});
