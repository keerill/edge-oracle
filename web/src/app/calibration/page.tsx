import type { Metadata } from "next";
import GlassCard from "@/components/GlassCard";
import styles from "@/components/reportLayout.module.scss";
import { getCalibration } from "@/lib/api/client";
import CalibrationView from "./CalibrationView";

export const metadata: Metadata = { title: "Calibration · EdgeOracle" };

// Server component: fetch + Zod-validate the calibration summary, then hand it to the view.
// Mirrors the signal detail page (direct typed-client call, no BFF route needed).
export default async function CalibrationPage() {
  try {
    const summary = await getCalibration();
    return <CalibrationView summary={summary} />;
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return (
      <section className={styles.page} aria-labelledby="calibration-heading">
        <div className={styles.head}>
          <div>
            <p className={styles.eyebrow}>
              <span className="mono">CALIBRATION</span> · keeping us honest
            </p>
            <h1 id="calibration-heading" className={styles.title}>
              Calibration
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
