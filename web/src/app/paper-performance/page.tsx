import type { Metadata } from "next";
import GlassCard from "@/components/GlassCard";
import styles from "@/components/reportLayout.module.scss";
import { getPaperPerformance } from "@/lib/api/client";
import PaperPerformanceView from "./PaperPerformanceView";

export const metadata: Metadata = { title: "Paper performance · EdgeOracle" };

// Server component: fetch + Zod-validate the paper-trading scorecard, then hand it to the view.
export default async function PaperPerformancePage() {
  try {
    const perf = await getPaperPerformance();
    return <PaperPerformanceView perf={perf} />;
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return (
      <section className={styles.page} aria-labelledby="paper-heading">
        <div className={styles.head}>
          <div>
            <p className={styles.eyebrow}>
              <span className="mono">PAPER TRADING</span> · no money at risk
            </p>
            <h1 id="paper-heading" className={styles.title}>
              Paper performance
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
