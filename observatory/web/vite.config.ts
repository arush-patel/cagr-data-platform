/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI runs on Vite's server and sends /api to `uv run observatory`.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8400" } },
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test-setup.ts"] },
});
