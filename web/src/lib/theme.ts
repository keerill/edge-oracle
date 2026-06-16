// Theme model + the no-flash bootstrap. Pure, framework-agnostic helpers so the
// resolution logic is unit-testable without rendering React.

export type Theme = "dark" | "light";

export const THEME_STORAGE_KEY = "edge-oracle-theme";

export function isTheme(value: unknown): value is Theme {
  return value === "dark" || value === "light";
}

/**
 * Resolve the active theme: an explicitly stored choice wins; otherwise fall
 * back to the OS preference (prefers-color-scheme). Defaults to dark.
 */
export function resolveTheme(
  stored: string | null,
  prefersLight: boolean,
): Theme {
  if (isTheme(stored)) return stored;
  return prefersLight ? "light" : "dark";
}

/**
 * Inline script (stringified) run in <head> before paint to set
 * documentElement[data-theme], avoiding a flash of the wrong theme. Kept tiny
 * and dependency-free because it executes before the bundle loads.
 */
export const NO_FLASH_SCRIPT = `(function(){try{var k=${JSON.stringify(
  THEME_STORAGE_KEY,
)};var s=localStorage.getItem(k);var t=(s==="dark"||s==="light")?s:(window.matchMedia&&window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark");document.documentElement.setAttribute("data-theme",t);}catch(e){document.documentElement.setAttribute("data-theme","dark");}})();`;
