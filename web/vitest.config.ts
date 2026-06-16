import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // We assert on roles/text/aria, not generated class names, so skip CSS
    // processing entirely — avoids compiling SCSS Modules in the test runner.
    css: false,
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
