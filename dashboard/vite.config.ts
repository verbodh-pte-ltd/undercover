import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Only `npm run dev` reads .env, from the repo root; the tests set their own values.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  envDir: mode === "development" ? ".." : false,
  server: {
    proxy: { "/api": process.env.BACKEND_URL ?? "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    env: {
      VITE_SUPABASE_URL: "http://supabase.test",
      VITE_SUPABASE_PUBLISHABLE_KEY: "test-key",
    },
    coverage: {
      include: ["src/**"],
      exclude: ["src/main.tsx"],
      thresholds: { statements: 80 },
    },
  },
}));
