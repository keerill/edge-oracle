import { test, expect } from "@playwright/test";

// Smoke e2e: the dashboard shell (banner + nav) renders on the pages that don't require a live
// quant backend. Catches build/routing/hydration regressions end-to-end in a real browser.

test("home page renders the app shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("banner")).toBeVisible();
  await expect(page.getByRole("navigation")).toBeVisible();
});

test("signals page renders (client component, backend-tolerant)", async ({ page }) => {
  await page.goto("/signals");
  // The shell renders even when the quant API is unreachable (client shows a reconnecting state).
  await expect(page.getByRole("banner")).toBeVisible();
  await expect(page.getByRole("main")).toBeVisible();
});

test("nav exposes the dashboard sections", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation");
  await expect(nav.getByRole("link", { name: /signals/i })).toBeVisible();
});
