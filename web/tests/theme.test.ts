import { describe, it, expect } from "vitest";
import {
  isTheme,
  resolveTheme,
  THEME_STORAGE_KEY,
  NO_FLASH_SCRIPT,
} from "@/lib/theme";

describe("isTheme", () => {
  it("accepts the two valid themes", () => {
    expect(isTheme("dark")).toBe(true);
    expect(isTheme("light")).toBe(true);
  });

  it("rejects anything else", () => {
    expect(isTheme("blue")).toBe(false);
    expect(isTheme(null)).toBe(false);
    expect(isTheme(undefined)).toBe(false);
    expect(isTheme("")).toBe(false);
  });
});

describe("resolveTheme", () => {
  it("prefers an explicitly stored choice over the OS preference", () => {
    expect(resolveTheme("light", false)).toBe("light");
    expect(resolveTheme("dark", true)).toBe("dark");
  });

  it("falls back to prefers-color-scheme when nothing is stored", () => {
    expect(resolveTheme(null, true)).toBe("light");
    expect(resolveTheme(null, false)).toBe("dark");
  });

  it("ignores a corrupt stored value and uses the OS preference", () => {
    expect(resolveTheme("garbage", true)).toBe("light");
    expect(resolveTheme("garbage", false)).toBe("dark");
  });

  it("defaults to dark with no stored value and no light preference", () => {
    expect(resolveTheme(null, false)).toBe("dark");
  });
});

describe("NO_FLASH_SCRIPT", () => {
  it("references the storage key and the prefers-color-scheme query", () => {
    expect(NO_FLASH_SCRIPT).toContain(THEME_STORAGE_KEY);
    expect(NO_FLASH_SCRIPT).toContain("prefers-color-scheme: light");
    expect(NO_FLASH_SCRIPT).toContain("data-theme");
  });
});
