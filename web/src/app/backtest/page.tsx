import type { Metadata } from "next";
import GlassCard from "@/components/GlassCard";
import styles from "@/components/reportLayout.module.scss";
import { getBacktest } from "@/lib/api/client";
import BacktestView from "./BacktestView";

export const metadata: Metadata = { title: "Backtest · EdgeOracle" };

// Server component: fetch + Zod-validate the backtest result, then hand it to the view.
export default async function BacktestPage() {
  try {
    const result = await getBacktest();
    return <BacktestView result={result} />;
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return (
      <section className={styles.page} aria-labelledby="backtest-heading">
        <div className={styles.head}>
          <div>
            <p className={styles.eyebrow}>
              <span className="mono">BACKTEST</span> · deterministic replay
            </p>
            <h1 id="backtest-heading" className={styles.title}>
              Backtest
            </h1>
          </div>
        </div>
        <GlassCard strong glow="magenta" className={styles.notice}>
          <strong>Couldn&apos;t reach the quant service.</strong>
          <span className={styles.noticeSub}>{message}</span>
        </GlassCard>
      </section>
    );
  }
}
