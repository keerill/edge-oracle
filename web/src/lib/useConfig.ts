"use client";

import { useCallback, useEffect, useState } from "react";
import { UserConfigSchema, type UserConfig } from "@/lib/schemas/config";

type Status = "loading" | "ready" | "saving" | "error";

// Load + persist the personal config via the BFF (/api/config). Centralizes the fetch/save so
// the settings page (and anyone else) shares one source of truth.
export function useConfig() {
  const [config, setConfig] = useState<UserConfig | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/config", { headers: { accept: "application/json" } });
        if (!res.ok) throw new Error(`config HTTP ${res.status}`);
        const cfg = UserConfigSchema.parse(await res.json());
        if (!cancelled) {
          setConfig(cfg);
          setStatus("ready");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unknown error");
          setStatus("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const save = useCallback(async (next: UserConfig) => {
    setStatus("saving");
    setError(null);
    try {
      const res = await fetch("/api/config", {
        method: "PUT",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify(next),
      });
      if (!res.ok) throw new Error(`save HTTP ${res.status}`);
      const saved = UserConfigSchema.parse(await res.json());
      setConfig(saved);
      setStatus("ready");
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
      setStatus("error");
      return false;
    }
  }, []);

  return { config, status, error, save };
}
