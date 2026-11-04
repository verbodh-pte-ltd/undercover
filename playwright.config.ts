import { defineConfig } from "@playwright/test";
import { API_KEY } from "./e2e/agent";

// The end-to-end tests run their own backend and dashboard on their own ports and database,
// so they never change the data behind `npm run dev`. A stand-in on port 8003 takes the
// payloads meant for the agent's AgentCore app, so they never reach the agent that `npm run dev` starts, one on port 8004
// takes Supabase Auth's, one on port 8005 takes the judge's, and one on port 8006 plays the
// Copilot, so no turn reaches the Copilot on port 8081 that a developer may be running.
const AUTH = "http://127.0.0.1:8004";
const JUDGE = "http://127.0.0.1:8005";
const BACKEND = "http://127.0.0.1:8001";
const COPILOT = "http://127.0.0.1:8006";

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  // One backend and database for every test, and a run is global state, so one at a time.
  workers: 1,
  use: { baseURL: "http://localhost:5174" },
  webServer: [
    {
      command: "node e2e/agent-stand-in.mjs",
      url: "http://127.0.0.1:8003/ping",
      // The app's address and the agent's key, to finish the runs it's given.
      env: { UNDERCOVER_API: BACKEND, UNDERCOVER_API_KEY: API_KEY },
      reuseExistingServer: true,
    },
    {
      command: "node e2e/auth-stand-in.mjs",
      url: "http://127.0.0.1:8004/health",
      reuseExistingServer: true,
    },
    {
      command: "node e2e/copilot-stand-in.mjs",
      url: `${COPILOT}/ping`,
      // The app's address, to play each turn through it with the handler's sign-in.
      env: { UNDERCOVER_API: BACKEND },
      reuseExistingServer: true,
    },
    {
      command: "node e2e/judge-stand-in.mjs",
      url: `${JUDGE}/health`,
      reuseExistingServer: true,
    },
    {
      command:
        `node -e "require('fs').rmSync('e2e/e2e.db', { force: true })"` +
        " && uv run -m uvicorn backend.main:app --port 8001",
      url: "http://127.0.0.1:8001/api/health",
      env: {
        UNDERCOVER_DB: "e2e/e2e.db",
        // Never local Postgres, even when DATABASE_URL is set in the shell.
        DATABASE_URL: "",
        UNDERCOVER_AGENT: "http://127.0.0.1:8003",
        // Never AgentCore Runtime, even when the shell names one.
        UNDERCOVER_AGENT_RUNTIME: "",
        UNDERCOVER_COPILOT: COPILOT,
        // Never the Copilot on AgentCore Runtime either.
        UNDERCOVER_COPILOT_RUNTIME: "",
        VITE_SUPABASE_URL: AUTH,
        JUDGE_URL: JUDGE,
        OPENROUTER_API_KEY: "e2e-key",
        // A fixed key, which the specs send as the agent's side would.
        UNDERCOVER_API_KEY: API_KEY,
        // The judge is called only off the free models.
        MODEL_NAME: "",
      },
      reuseExistingServer: true,
    },
    {
      // Not the development mode, so Vite never reads .env.
      command: "npx vite dashboard --port 5174 --strictPort --mode e2e",
      url: "http://localhost:5174",
      env: {
        BACKEND_URL: "http://127.0.0.1:8001",
        VITE_SUPABASE_URL: AUTH,
        VITE_SUPABASE_PUBLISHABLE_KEY: "e2e-publishable-key",
      },
      reuseExistingServer: true,
    },
  ],
});
