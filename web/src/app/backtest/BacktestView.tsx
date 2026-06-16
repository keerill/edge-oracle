import GlassCard from "@/components/GlassCard";
import EquityCurve from "@/components/charts/EquityCurve";
import MonteCarloChart from "@/components/charts/MonteCarloChart";
import type { BacktestResult } from "@/lib/schemas/report";
import { fmtPct, fmtUsd } from "@/lib/format";
import styles from "@/components/reportLayout.module.scss";

// Presentational backtest report — the deterministic replay (equity, drawdown, hit rate) plus
// the Monte-Carlo distribution of final bankroll (variance, not just the median). Renders from
// props; the page does the fetching.

const prettyStrategy = (key: string): string =>
  key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

const orDash = (n: number | null, f: (v: number) => string): string => (n === null ? "—" : f(n));

export default function BacktestView({ result }: { result: BacktestResult }) {
  const hasBets = result.n_bets > 0;

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
      <p className={styles.lede}>
        A causal replay of the engine over stored prices, costs baked into every fill. The equity
        curve and hit rate are the single realized path; the Monte-Carlo panel resamples outcomes
        to show the <em>distribution</em> of where the bankroll could land — not just the median.
      </p>

      <section className={styles.metrics} aria-label="Headline results">
        <Metric label="Final bankroll" value={fmtUsd(result.final_bankroll)} hint={`from ${fmtUsd(result.initial_bankroll)}`} accent />
        <Metric label="Total return" value={fmtPct(result.total_return)} />
        <Metric label="Hit rate" value={orDash(result.hit_rate, fmtPct)} />
        <Metric label="Max drawdown" value={fmtPct(result.max_drawdown)} />
        <Metric label="Sharpe-like" value={orDash(result.sharpe_like, (v) => v.toFixed(2))} />
        <Metric label="Bets" value={String(result.n_bets)} />
      </section>

      {!hasBets ? (
        <GlassCard strong className={styles.notice}>
          <strong>No resolved bets in this replay.</strong>
          <span className={styles.noticeSub}>
            Configure a market-outcome feed (EDGE_BACKTEST_RESOLUTIONS_PATH) to populate the equity
            curve and the Monte-Carlo distribution.
          </span>
        </GlassCard>
      ) : (
        <>
          <GlassCard strong className={styles.chartCard}>
            <h2 className={styles.cardTitle}>Equity curve</h2>
            <p className={styles.cardSub}>
              Realized bankroll at each resolution; the shaded band is the worst peak-to-trough
              drawdown ({fmtPct(result.max_drawdown)}).
            </p>
            <EquityCurve curve={result.equity_curve} initial={result.initial_bankroll} />
          </GlassCard>

          {result.monte_carlo && (
            <GlassCard strong glow="cyan" className={styles.chartCard}>
              <h2 className={styles.cardTitle}>Monte-Carlo distribution</h2>
              <p className={styles.cardSub}>
                Final bankroll across resampled simulations: p5–p95 whisker, p25–p75 box, median
                line, mean dot. Anything left of the start line is a loss.
              </p>
              <MonteCarloChart mc={result.monte_carlo} initial={result.initial_bankroll} />
              <p className={styles.caption}>
                Across <b>{result.monte_carlo.n_sims}</b> simulations: probability of loss{" "}
                <b>{fmtPct(result.monte_carlo.prob_loss)}</b>, median final{" "}
                <b>{fmtUsd(result.monte_carlo.final_bankroll_median)}</b>, 5th–95th{" "}
                <b>
                  {fmtUsd(result.monte_carlo.final_bankroll_p5)}–
                  {fmtUsd(result.monte_carlo.final_bankroll_p95)}
                </b>
                , median max drawdown <b>{fmtPct(result.monte_carlo.median_max_drawdown)}</b>.
              </p>
            </GlassCard>
          )}

          <GlassCard strong className={styles.tableCard}>
            <h2 className={styles.cardTitle}>By strategy</h2>
            <table className={styles.table}>
              <caption className="sr-only">Per-strategy backtest breakdown.</caption>
              <thead>
                <tr>
                  <th scope="col">Strategy</th>
                  <th scope="col">Bets</th>
                  <th scope="col">Hit rate</th>
                  <th scope="col">P&amp;L</th>
                  <th scope="col">Return</th>
                  <th scope="col">Sharpe</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(result.per_strategy).map(([key, s]) => (
                  <tr key={key}>
                    <td>{prettyStrategy(key)}</td>
                    <td>{s.n}</td>
                    <td>{orDash(s.hit_rate, fmtPct)}</td>
                    <td>{fmtUsd(s.total_pnl)}</td>
                    <td>{fmtPct(s.total_return)}</td>
                    <td>{orDash(s.sharpe_like, (v) => v.toFixed(2))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </GlassCard>
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
