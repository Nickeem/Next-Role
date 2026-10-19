/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

declare const process: { env: Record<string, string | undefined> };

// In development the API runs separately (uvicorn on :8000); in production FastAPI serves
// the built files from the same origin, so the session cookie and CSRF header just work.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": process.env.VITE_API_URL ?? "http://localhost:8000" },
  },
  build: { outDir: "dist", sourcemap: false },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
