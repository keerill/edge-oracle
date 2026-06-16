import Badge, { type BadgeVariant } from "@/components/Badge";
import GlassCard from "@/components/GlassCard";
import MetricTimeline from "@/components/charts/MetricTimeline";
import ReliabilityChart from "@/components/charts/ReliabilityChart";
import type { CalibrationSummary } from "@/lib/schemas/report";
import { fmtPct } from "@/lib/format";
import styles from "@/components/reportLayout.module.scss";

// Presentational calibration report — takes the validated summary (or null on an empty journal)
// and renders the reliability curve, the Brier/log-loss timeline, the per-strategy scores, and
// the suggested Kelly-fraction adjustment up top. No data fetching here (the page does that), so
// it renders deterministically from props in tests.

const prettyStrategy = (key: string): string =>
  key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

const fmt = (n: number, digits = 4): string => n.toFixed(digits);

function Header() {
  return (
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
  );
}

export default function CalibrationView({ summary }: { summary: CalibrationSummary }) {
  if (summary === null) {
    return (
      <section className={styles.page} aria-labelledby="calibration-heading">
        <Header />
        <p className={styles.lede}>
          How well the models&apos; probabilities actually held up — reliability, Brier and
          log-loss, and the Kelly-fraction shrink they imply.
        </p>
        <GlassCard strong className={styles.notice}>
          <strong>No resolved markets journaled yet.</strong>
          <span className={styles.noticeSub}>
            Calibration needs resolved predictions; the curve appears once the journal has rows.
          </span>
        </GlassCard>
      </section>
    );
  }

  const k = summary.kelly;
  const shrinking = k.multiplier !== null && k.multiplier < 1;
  const base =
    k.adjusted_frac !== null && k.multiplier !== null && k.multiplier !== 0
      ? k.adjusted_frac / k.multiplier
      : 0.25;

  const kellyBadge: { variant: BadgeVariant; label: string } =
    k.adjusted_frac === null
      ? { variant: "neutral", label: "Insufficient data" }
      : shrinking
        ? { variant: "watch", label: "Overconfident — shrinking" }
        : { variant: "pass", label: "Well-calibrated" };

  return (
    <section className={styles.page} aria-labelledby="calibration-heading">
      <Header />
      <p className={styles.lede}>
        How well the models&apos; probabilities actually held up. A point on the diagonal is
        perfectly calibrated; points below it are overconfident. Lower Brier and log-loss are
        better — and overconfidence in the high-confidence bins shrinks the Kelly fraction.
      </p>

      {/* Headline: the suggested Kelly-fraction adjustment, surfaced prominently. */}
      <GlassCard strong glow={shrinking ? "magenta" : "violet"} className={styles.kellyCard}>
        <div className={styles.kellyTop}>
          <h2 className={styles.kellyHeading}>Suggested fractional Kelly</h2>
          <Badge variant={kellyBadge.variant} dot>
            {kellyBadge.label}
          </Badge>
        </div>
        <div className={styles.kellyBig}>{k.adjusted_frac === null ? base.toFixed(2) : fmt(k.adjusted_frac, 3)}</div>
        <p className={styles.kellySub}>
          {k.adjusted_frac === null ? (
            <>
              Not enough high-confidence resolutions to adjust yet — holding the{" "}
              <span className="mono">{base.toFixed(2)}</span> base fraction.
            </>
          ) : (
            <>
              Shrunk <span className="mono">×{fmt(k.multiplier ?? 1, 2)}</span> from the{" "}
              <span className="mono">{base.toFixed(2)}</span> base by overconfidence in the
              high-confidence bins.
            </>
          )}
        </p>
        {k.adjusted_frac !== null && (
          <dl className={styles.diagGrid}>
            <div className={styles.diag}>
              <dt className={styles.diagLabel}>High-conf records</dt>
              <dd className={styles.diagValue}>{k.n_high_conf}</dd>
            </div>
            <div className={styles.diag}>
              <dt className={styles.diagLabel}>Claimed avg</dt>
              <dd className={styles.diagValue}>{k.claimed_avg === null ? "—" : fmtPct(k.claimed_avg)}</dd>
            </div>
            <div className={styles.diag}>
              <dt className={styles.diagLabel}>Realized avg</dt>
              <dd className={styles.diagValue}>{k.realized_avg === null ? "—" : fmtPct(k.realized_avg)}</dd>
            </div>
            <div className={styles.diag}>
              <dt className={styles.diagLabel}>Worst bin ×</dt>
              <dd className={styles.diagValue}>
                {k.worst_bin_multiplier === null ? "—" : fmt(k.worst_bin_multiplier, 2)}
              </dd>
            </div>
          </dl>
        )}
      </GlassCard>

      {/* Scalar scores. */}
      <section className={styles.metrics} aria-label="Overall scores">
        <Metric label="Brier score" value={fmt(summary.overall.brier)} hint="mean squared error" />
        <Metric label="Log-loss" value={fmt(summary.overall.log_loss)} hint="nats" />
        <Metric label="Resolved records" value={String(summary.overall.n)} />
      </section>

      <div className={styles.grid2}>
        <GlassCard strong className={styles.chartCard}>
          <h2 className={styles.cardTitle}>Reliability curve</h2>
          <p className={styles.cardSub}>Claimed probability vs realized frequency; dot size ∝ sample count.</p>
          <ReliabilityChart bins={summary.reliability} />
        </GlassCard>

        <GlassCard strong className={styles.chartCard}>
          <h2 className={styles.cardTitle}>Scores over time</h2>
          <p className={styles.cardSub}>Cumulative Brier &amp; log-loss as the journal grows.</p>
          <MetricTimeline points={summary.timeline} />
        </GlassCard>
      </div>

      <GlassCard strong className={styles.tableCard}>
        <h2 className={styles.cardTitle}>By strategy</h2>
        <table className={styles.table}>
          <caption className="sr-only">Brier and log-loss per strategy.</caption>
          <thead>
            <tr>
              <th scope="col">Strategy</th>
              <th scope="col">Records</th>
              <th scope="col">Brier</th>
              <th scope="col">Log-loss</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(summary.per_strategy).map(([key, m]) => (
              <tr key={key}>
                <td>{prettyStrategy(key)}</td>
                <td>{m.n}</td>
                <td>{fmt(m.brier)}</td>
                <td>{fmt(m.log_loss)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </GlassCard>
    </section>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <GlassCard className={styles.metric}>
      <span className={styles.metricLabel}>{label}</span>
      <span className={`${styles.metricValue} mono`}>{value}</span>
      {hint && <span className={styles.noticeSub}>{hint}</span>}
    </GlassCard>
  );
}
