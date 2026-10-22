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
  build: {
    outDir: "dist",
    sourcemap: false,
    // print.html is the renderer's print page (ADR 0007); it shares the template components.
    rollupOptions: { input: { main: "index.html", print: "print.html" } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
