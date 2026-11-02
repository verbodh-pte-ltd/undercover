import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Only `npm run dev` and `npm run build` read .env, from the repo root; the build also
// takes VITE_ variables from the shell. The tests set their own values.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  envDir: ["development", "production"].includes(mode) ? ".." : false,
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
