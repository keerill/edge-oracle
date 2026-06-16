"use client";

import { useEffect, useState } from "react";
import Badge from "@/components/Badge";
import GlassCard from "@/components/GlassCard";
import { AdvisedSignalListSchema, type AdvisedSignal } from "@/lib/schemas/signal";
import SignalsTable from "./SignalsTable";
import styles from "./page.module.scss";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; signals: AdvisedSignal[] };

export default function SignalsPage() {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/signals", { headers: { accept: "application/json" } });
        if (!res.ok) throw new Error(`Failed to load signals (HTTP ${res.status})`);
        const signals = AdvisedSignalListSchema.parse(await res.json());
        if (!cancelled) setState({ status: "ready", signals });
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
          <Badge variant="neutral" dot>
            REST · streaming in phase 4
          </Badge>
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
