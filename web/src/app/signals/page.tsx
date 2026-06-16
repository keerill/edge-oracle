"use client";

import { useEffect, useRef, useState } from "react";
import Badge from "@/components/Badge";
import GlassCard from "@/components/GlassCard";
import { useNotifications } from "@/components/NotificationsProvider";
import { AdvisedSignalListSchema, AdvisedSignalSchema, type AdvisedSignal } from "@/lib/schemas/signal";
import { shouldToastSignal } from "@/lib/notifications";
import { mergeSignal } from "@/lib/stream";
import SignalsTable from "./SignalsTable";
import styles from "./page.module.scss";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; signals: AdvisedSignal[] };

export default function SignalsPage() {
  const [state, setState] = useState<State>({ status: "loading" });
  const [live, setLive] = useState(false);
  const { push } = useNotifications();
  // Last-seen signal per id, so an opportunity toast fires only on a rising edge (not every tick).
  const lastSeen = useRef(new Map<string, AdvisedSignal>());

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/signals", { headers: { accept: "application/json" } });
        if (!res.ok) throw new Error(`Failed to load signals (HTTP ${res.status})`);
        const signals = AdvisedSignalListSchema.parse(await res.json());
        if (!cancelled) {
          lastSeen.current = new Map(signals.map((s) => [s.id, s]));
          setState({ status: "ready", signals });
        }
      } catch (err) {
        if (!cancelled) {
          setState({ status: "error", message: err instanceof Error ? err.message : "Unknown error" });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Live updates: subscribe to the server-conflated SSE stream and merge each signal in place.
  useEffect(() => {
    const source = new EventSource("/api/stream");
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false); // browser auto-reconnects; fall back to the REST view
    source.onmessage = (event) => {
      const parsed = AdvisedSignalSchema.safeParse(JSON.parse(event.data));
      if (!parsed.success) return;
      const incoming = parsed.data;
      // Opportunity toast on a rising edge across the high-net-edge threshold.
      if (shouldToastSignal(lastSeen.current.get(incoming.id), incoming)) {
        push({
          severity: "success",
          title: "High-net-edge signal",
          detail: `${incoming.market_question ?? incoming.market_id} · net ${(incoming.net_edge * 100).toFixed(1)}%`,
        });
      }
      lastSeen.current.set(incoming.id, incoming);
      setState((prev) =>
        prev.status === "ready"
          ? { status: "ready", signals: mergeSignal(prev.signals, incoming) }
          : prev,
      );
    };
    return () => {
      source.close();
      setLive(false);
    };
  }, [push]);

  return (
    <section aria-labelledby="signals-heading">
      <div className={styles.head}>
        <div>
          <p className={styles.eyebrow}>
            <span className="mono">POLYMARKET</span> · live edges
          </p>
          <h1 id="signals-heading" className={styles.title}>
            Open signals
          </h1>
        </div>
        <div className={styles.headMeta}>
          {state.status === "ready" && (
            <Badge variant="accent">
              {state.signals.length} {state.signals.length === 1 ? "signal" : "signals"}
            </Badge>
          )}
          {live ? (
            <Badge variant="accent" dot pulse>
              streaming · live
            </Badge>
          ) : (
            <Badge variant="neutral" dot>
              REST · reconnecting…
            </Badge>
          )}
        </div>
      </div>

      <p className={styles.lede}>
        Ranked by net-of-cost edge. Each row is sized with fractional Kelly and gated on the CI
        lower bound clearing fees, slippage and gas — the advisor proposes, you decide. Click a
        market for the full sizing breakdown.
      </p>

      {state.status === "loading" && (
        <GlassCard strong className={styles.notice}>
          Loading signals…
        </GlassCard>
      )}
      {state.status === "error" && (
        <GlassCard strong glow="magenta" className={styles.notice}>
          <strong>Couldn’t reach the quant service.</strong>
          <span className={styles.noticeSub}>{state.message}</span>
        </GlassCard>
      )}
      {state.status === "ready" && <SignalsTable signals={state.signals} />}
    </section>
  );
}
