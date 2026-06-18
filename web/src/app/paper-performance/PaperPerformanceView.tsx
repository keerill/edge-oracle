import GlassCard from "@/components/GlassCard";
import EquityCurve from "@/components/charts/EquityCurve";
import type { PaperPerformance } from "@/lib/schemas/report";
import { fmtPct, fmtSeconds, fmtUsd, fmtUsdSigned } from "@/lib/format";
import styles from "@/components/reportLayout.module.scss";

// Presentational paper-trading scorecard — the no-money validation track. The advisor logs the
// bets it *would* place, then scores them against real outcomes. Renders from props; the page
// fetches. The directional track is outcome-verified; set-arb P&L is fill-optimistic (caveat).

const prettyStrategy = (key: string): string =>
  key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

const orDash = (n: number | null, f: (v: number) => string): string => (n === null ? "—" : f(n));

export default function PaperPerformanceView({ perf }: { perf: PaperPerformance }) {
  const hasSettled = perf.n_closed > 0;

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
      <p className={styles.lede}>
        The advisor auto-logs the bets it <em>would</em> place — sized exactly as the dashboard
        recommends — then settles them against real market outcomes. This is the honest precondition
        to risking capital: an edge has to survive fees, spread, slippage and gas <em>here</em>{" "}
        first. Read the per-strategy split, not just the headline.
      </p>

      <section className={styles.metrics} aria-label="Headline results">
        <Metric label="Final bankroll" value={fmtUsd(perf.final_bankroll)} hint={`from ${fmtUsd(perf.initial_bankroll)}`} accent />
        <Metric label="Total P&L" value={fmtUsdSigned(perf.total_pnl)} />
        <Metric label="Total return" value={fmtPct(perf.total_return)} />
        <Metric label="Hit rate" value={orDash(perf.hit_rate, fmtPct)} />
        <Metric label="Max drawdown" value={fmtPct(perf.max_drawdown)} />
        <Metric label="Sharpe-like" value={orDash(perf.sharpe_like, (v) => v.toFixed(2))} />
        <Metric label="Settled bets" value={String(perf.n_closed)} />
        <Metric label="Open bets" value={String(perf.n_open)} hint="awaiting resolution" />
      </section>

      {!hasSettled ? (
        <GlassCard strong className={styles.notice}>
          <strong>No paper trades have settled yet.</strong>
          <span className={styles.noticeSub}>
            {perf.n_open} open and waiting to resolve. Run the capture loop
            (<span className="mono">python -m app.paper.engine loop</span>) and the resolution loop
            (<span className="mono">python -m app.ingestion.resolution_engine loop</span>), then this
            scorecard fills in as markets resolve — typically over 2–4 weeks.
          </span>
        </GlassCard>
      ) : (
        <>
          <GlassCard strong className={styles.chartCard}>
            <h2 className={styles.cardTitle}>Realized equity</h2>
            <p className={styles.cardSub}>
              Opening bankroll plus cumulative realized P&amp;L, sampled at each settlement; the
              shaded band is the worst peak-to-trough drawdown ({fmtPct(perf.max_drawdown)}).
            </p>
            <EquityCurve curve={perf.equity_curve} initial={perf.initial_bankroll} />
          </GlassCard>

          <GlassCard strong className={styles.tableCard}>
            <h2 className={styles.cardTitle}>By strategy</h2>
            <table className={styles.table}>
              <caption className="sr-only">Per-strategy paper-trading breakdown.</caption>
              <thead>
                <tr>
                  <th scope="col">Strategy</th>
                  <th scope="col">Settled</th>
                  <th scope="col">Wins</th>
                  <th scope="col">Hit rate</th>
                  <th scope="col">P&amp;L</th>
                  <th scope="col">Avg return</th>
                  <th scope="col">Sharpe</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(perf.per_strategy).map(([key, s]) => (
                  <tr key={key}>
                    <td>{prettyStrategy(key)}</td>
                    <td>{s.n}</td>
                    <td>{s.wins}</td>
                    <td>{orDash(s.hit_rate, fmtPct)}</td>
                    <td>{fmtUsdSigned(s.total_pnl)}</td>
                    <td>{orDash(s.avg_return, fmtPct)}</td>
                    <td>{orDash(s.sharpe_like, (v) => v.toFixed(2))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </GlassCard>

          {perf.arb_fill.checked > 0 && (
            <GlassCard strong className={styles.tableCard}>
              <h2 className={styles.cardTitle}>Arb fill survival</h2>
              <p className={styles.cardSub}>
                Each set-arb is re-priced on a fresh book the moment it&apos;s captured; this covers
                every re-checked arb — open, settled and expired. Arbs skipped on a fetch error
                aren&apos;t counted. A high survival rate is what makes the arb P&amp;L trustworthy.
              </p>
              <div className={styles.metrics}>
                <Metric label="Survival rate" value={orDash(perf.arb_fill.survival_rate, fmtPct)} accent />
                <Metric label="Verified" value={`${perf.arb_fill.verified} / ${perf.arb_fill.checked}`} />
                <Metric label="Expired" value={String(perf.arb_fill.expired)} hint="edge gone on re-check" />
                <Metric label="Avg re-check latency" value={orDash(perf.arb_fill.avg_latency_s, fmtSeconds)} />
              </div>
            </GlassCard>
          )}

          {perf.arb_fill_assumed && (
            <GlassCard glow="amber" className={styles.notice}>
              <strong>Some set-arb P&amp;L is fill-optimistic.</strong>
              <span className={styles.noticeSub}>
                One or more settled set-arbs predate the fill-check or weren&apos;t re-confirmed on a
                fresh book, so their locked edge assumes the dislocation was still fillable. Read
                those as a ceiling; the outcome-verified directional track and the fill-survival
                numbers above are the ones to trust.
              </span>
            </GlassCard>
          )}
        </>
      )}
    </section>
  );
}

function Metric({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: string;
  hint?: string;
  accent?: boolean;
}) {
  return (
    <GlassCard className={styles.metric}>
      <span className={styles.metricLabel}>{label}</span>
      <span className={`${styles.metricValue} mono ${accent ? styles.metricAccent : ""}`}>{value}</span>
      {hint && <span className={styles.noticeSub}>{hint}</span>}
    </GlassCard>
  );
}
