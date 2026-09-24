import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

// Real Chrome, real Keycloak (docker compose in infra/platform), the real API and the real Vite dev server.
// Nothing here is mocked: `npm run e2e` needs the platform stack up (see infra/platform/up.sh).
const python = resolve(process.cwd(), "..", "..", ".venv", "Scripts", "python.exe");
const serve = resolve(process.cwd(), "..", "backend", "api", "serve.py");
const serveDemo = resolve(process.cwd(), "..", "backend", "scenario_control", "serve.py");

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "test-results/report.json" }]],
  use: {
    baseURL: "http://localhost:5173",
    channel: "chrome",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1366, height: 768 },
  },
  webServer: [
    {
      command: `"${python}" "${serve}" --port 8100`,
      url: "http://127.0.0.1:8100/api/v1/health",
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      command: `"${python}" "${serveDemo}" --port 8101`,
      url: "http://127.0.0.1:8101/scenario-control/v1/health",
      reuseExistingServer: true,
      timeout: 60_000,
    },
    { command: "npm run dev", url: "http://localhost:5173", reuseExistingServer: true, timeout: 60_000 },
  ],
});
