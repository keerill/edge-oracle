import Link from "next/link";
import { notFound } from "next/navigation";
import Badge from "@/components/Badge";
import EdgeMeter from "@/components/EdgeMeter";
import GlassCard from "@/components/GlassCard";
import { getSignal, QuantApiError } from "@/lib/api/client";
import type { AdvisedSignal } from "@/lib/schemas/signal";
import {
  edgeToBps,
  fmtPct,
  fmtPrice,
  fmtUsd,
  fmtUsdSigned,
  sideLabel,
  signalStatus,
  strategyLabel,
} from "@/lib/format";
import styles from "./page.module.scss";

const STATUS_LABEL = { pass: "Gate ✓ pass", watch: "Below gate", gated: "Gated" } as const;

// Server component: the dedicated, deep-linkable detail view backed by GET /signals/{id}.
export default async function SignalDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let signal: AdvisedSignal;
  try {
    signal = await getSignal(id);
  } catch (err) {
    if (err instanceof QuantApiError && err.status === 404) notFound();
    throw err;
  }

  const status = signalStatus(signal);

  // Deep-link to the portfolio "record bet" form, prefilled from this signal.
  const trackSide =
    signal.strategy === "set_arb" ? "set" : signal.kind === "buy_yes" ? "yes" : "no";
  const trackParams = new URLSearchParams({
    market_id: signal.market_id,
    condition_id: signal.condition_id,
    strategy: signal.strategy,
    side: trackSide,
    ...(signal.economics?.ask != null ? { entry_price: String(signal.economics.ask) } : {}),
    ...(signal.economics?.stake_usd
      ? { stake_usd: String(signal.economics.stake_usd) }
      : {}),
  });

  return (
    <article className={styles.detail}>
      <Link href="/signals" className={styles.back}>
        ← All signals
      </Link>

      <header className={styles.head}>
        <div className={styles.headTop}>
          <Badge variant="neutral">{sideLabel(signal.kind)}</Badge>
          <span className={styles.strategy}>{strategyLabel(signal.strategy)}</span>
          <Badge variant={status}>{STATUS_LABEL[status]}</Badge>
        </div>
        <h1 className={styles.title}>{signal.market_question ?? signal.condition_id}</h1>
        <p className={styles.meta}>
          <span className="mono">{signal.condition_id}</span>
          <span aria-hidden="true"> · </span>
          <time dateTime={signal.time}>{signal.time.replace("T", " ").replace("+00:00", " UTC")}</time>
        </p>
        <Link href={`/portfolio?${trackParams.toString()}`} className={styles.track}>
          + Track this bet
        </Link>
      </header>

      <section className={styles.metrics} aria-label="Edge and sizing">
        <Metric label="Market price (m)" value={fmtPrice(signal.market_price)} />
        <Metric label="Your p" value={signal.p === null ? "—" : fmtPrice(signal.p)} />
        <Metric label="Edge" value={signal.edge === 0 ? "—" : fmtPct(signal.edge)} />
        <Metric label="Net-of-cost edge" value={fmtPct(signal.net_edge)} accent />
        <Metric label="Confidence" value={fmtPct(signal.confidence)} />
        <Metric
          label="Recommended size"
          value={
            signal.recommended_size_usd > 0
              ? `${fmtUsd(signal.recommended_size_usd)} · ${fmtPct(signal.recommended_size_pct)}`
              : "—"
          }
          accent
        />
      </section>

      {signal.economics ? (
        <GlassCard
          strong
          glow={signal.strategy === "set_arb" ? "green" : "violet"}
          className={styles.gateCard}
        >
          <h2 className={styles.cardTitle}>Earnings &amp; risk (your bankroll)</h2>
          {signal.strategy === "set_arb" ? (
            <>
              <p className={styles.gateLede}>
                Risk-free: a complete set redeems for $1.00, so this profit is locked regardless of
                how the market resolves.
              </p>
              <dl className={styles.breakdown}>
                <Row
                  label="Locked profit (per set detected)"
                  value={fmtUsd(signal.economics.locked_profit_usd ?? 0)}
                  strong
                />
                <Row label="Probability of loss" value="0% — risk-free" />
              </dl>
            </>
          ) : (
            <>
              <p className={styles.gateLede}>
                What this bet makes or loses at your recommended stake. EV is positive on average,
                but a single bet can still land on the loss leg — sizing keeps that survivable.
              </p>
              <dl className={styles.breakdown}>
                <Row label="Recommended stake" value={fmtUsd(signal.economics.stake_usd ?? 0)} />
                <Row
                  label="Profit if it wins"
                  value={fmtUsdSigned(signal.economics.profit_if_win_usd ?? 0)}
                />
                <Row
                  label="Loss if it loses"
                  value={fmtUsdSigned(signal.economics.profit_if_loss_usd ?? 0)}
                />
                <Row
                  label="Expected value (at your p)"
                  value={fmtUsdSigned(signal.economics.ev_usd ?? 0)}
                  strong
                />
                <Row
                  label="Expected value (conservative, p_lo)"
                  value={fmtUsdSigned(signal.economics.ev_usd_conservative ?? 0)}
                />
                <Row
                  label="Probability of loss"
                  value={fmtPct(signal.economics.prob_of_loss ?? 0)}
                />
              </dl>
            </>
          )}
        </GlassCard>
      ) : null}

      <GlassCard strong className={styles.meterCard}>
        <h2 className={styles.cardTitle}>Net edge after costs</h2>
        <EdgeMeter edgeBps={edgeToBps(signal.net_edge)} thresholdBps={0} label="Net edge" />
      </GlassCard>

      {signal.gate ? (
        <GlassCard strong glow={status === "pass" ? "green" : "magenta"} className={styles.gateCard}>
          <h2 className={styles.cardTitle}>Cost gate</h2>
          <p className={styles.gateLede}>
            A directional bet clears only when the CI lower bound beats the all-in break-even
            (strict <span className="mono">&gt;</span>). Sizing then runs fractional Kelly on the
            ask, hard-capped per position.
          </p>
          <dl className={styles.breakdown}>
            <Row label="Side price (m)" value={fmtPrice(signal.gate.m)} />
            <Row label="+ half-spread" value={fmtPrice(signal.gate.half_spread)} />
            <Row label="+ slippage" value={fmtPrice(signal.gate.slippage)} />
            <Row label="+ gas" value={fmtPrice(signal.gate.gas)} />
            <Row label="= break-even threshold" value={fmtPrice(signal.gate.threshold)} strong />
            <Row
              label={`p_lo (p − ${fmtPrice(signal.gate.margin)} margin)`}
              value={fmtPrice(signal.gate.p_lo)}
            />
          </dl>
          <p className={`${styles.verdict} ${styles[status]}`}>
            <span className="mono">
              p_lo {fmtPrice(signal.gate.p_lo)} {signal.gate_passed ? ">" : "≤"} threshold{" "}
              {fmtPrice(signal.gate.threshold)}
            </span>{" "}
            — {signal.gate_passed ? "gate passes, bet sized." : "gate fails, no bet."}
          </p>
        </GlassCard>
      ) : signal.strategy === "set_arb" ? (
        <GlassCard strong glow="green" className={styles.gateCard}>
          <h2 className={styles.cardTitle}>Risk-free set arbitrage</h2>
          <p className={styles.gateLede}>
            A complete set (1 YES + 1 NO) redeems for exactly $1.00. The locked net edge of{" "}
            <span className="mono">{fmtPct(signal.net_edge)}</span> per set is already net of gas
            and slippage and is outcome-independent — no Kelly gate applies (confidence 100%).
            Live set sizing is gated to the execution module (advisor only).
          </p>
        </GlassCard>
      ) : (
        <GlassCard strong className={styles.gateCard}>
          <h2 className={styles.cardTitle}>Display-only heuristic</h2>
          <p className={styles.gateLede}>
            The favourite–longshot bias has no probability estimate, so there is no Kelly-sized
            bet this slice — only a normalized strength (confidence{" "}
            <span className="mono">{fmtPct(signal.confidence)}</span>). It is surfaced for context,
            not as an actionable stake.
          </p>
        </GlassCard>
      )}
    </article>
  );
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <GlassCard className={styles.metric}>
      <span className={styles.metricLabel}>{label}</span>
      <span className={`${styles.metricValue} mono ${accent ? styles.metricAccent : ""}`}>
        {value}
      </span>
    </GlassCard>
  );
}

function Row({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={`${styles.row} ${strong ? styles.rowStrong : ""}`}>
      <dt>{label}</dt>
      <dd className="mono">{value}</dd>
    </div>
  );
}
