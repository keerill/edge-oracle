"use client";

import { useEffect, useState } from "react";
import {
  THEME_STORAGE_KEY,
  isTheme,
  resolveTheme,
  type Theme,
} from "@/lib/theme";
import styles from "./ThemeToggle.module.scss";

/**
 * Flips [data-theme] on <html> and persists the choice. Initial value is read
 * from the attribute the no-flash script already set, so there's no mismatch.
 */
export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("dark");

  // Sync state with whatever the bootstrap script resolved at load.
  useEffect(() => {
    const current = document.documentElement.getAttribute("data-theme");
    if (isTheme(current)) {
      setTheme(current);
    } else {
      const prefersLight = window.matchMedia(
        "(prefers-color-scheme: light)",
      ).matches;
      setTheme(resolveTheme(localStorage.getItem(THEME_STORAGE_KEY), prefersLight));
    }
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      /* storage may be unavailable (private mode) — theme still applies live */
    }
  }

  const isDark = theme === "dark";

  return (
    <button
      type="button"
      className={styles.toggle}
      onClick={toggle}
      role="switch"
      aria-checked={isDark}
      aria-label={`Switch to ${isDark ? "light" : "dark"} theme`}
      title={`Switch to ${isDark ? "light" : "dark"} theme`}
    >
      <span className={styles.track} aria-hidden="true">
        <span className={styles.thumb}>{isDark ? "☾" : "☀"}</span>
      </span>
      <span className={styles.label}>{isDark ? "Dark" : "Light"}</span>
    </button>
  );
}
