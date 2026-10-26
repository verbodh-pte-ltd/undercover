import { defineConfig } from "@playwright/test";

// The end-to-end tests run their own backend and dashboard on their own ports and database,
// so they never change the data behind `npm run dev`.
export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  // One backend and database for every test, and a run is global state, so one at a time.
  workers: 1,
  use: { baseURL: "http://localhost:5174" },
  webServer: [
    {
      command:
        `node -e "require('fs').rmSync('e2e/e2e.db', { force: true })"` +
        " && uv run -m uvicorn backend.main:app --port 8001",
      url: "http://127.0.0.1:8001/api/health",
      env: { UNDERCOVER_DB: "e2e/e2e.db" },
      reuseExistingServer: true,
    },
    {
      command: "npx vite dashboard --port 5174 --strictPort",
      url: "http://localhost:5174",
      env: { BACKEND_URL: "http://127.0.0.1:8001" },
      reuseExistingServer: true,
    },
  ],
});
