import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The backend runs on :8000 (see CLAUDE.md). Everything under /api is proxied
// so the browser never needs CORS in development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
