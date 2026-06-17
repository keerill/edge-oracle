import { defineConfig, devices } from "@playwright/test";

// Web-only e2e: drives the production server (`next start`) with no quant backend. The home page
// is static and the Signals page is a client component with a graceful "reconnecting" state, so
// both render the dashboard shell without a backend. (Deeper data-page e2e against a seeded
// quant stack — calibration/backtest server components — is a follow-up.)
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // Run the next binary directly — avoids the pnpm launcher (the pinned pnpm@9.15.0 vs the
    // system pnpm differ across Node versions; next itself runs on Node >=20).
    command: "node_modules/.bin/next start",
    url: "http://localhost:3000",
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
  },
});
