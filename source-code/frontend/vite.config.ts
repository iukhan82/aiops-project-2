import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser talks only to this origin; /api (and the separate scenario-control API) are proxied to the
// backends, so there is no CORS surface and the Bearer token never goes cross-origin except to Keycloak.
const api = process.env.AIOPS_API_TARGET ?? "http://127.0.0.1:8100";
const scenario = process.env.AIOPS_SCENARIO_TARGET ?? "http://127.0.0.1:8101";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: api, changeOrigin: false, ws: true },
      "/scenario-control": { target: scenario, changeOrigin: false },
    },
  },
  preview: { host: "localhost", port: 4173, strictPort: true },
  build: { sourcemap: true, target: "es2022" },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
