import GlassCard from "@/components/GlassCard";
import Badge, { type BadgeVariant } from "@/components/Badge";
import EdgeMeter, { type EdgeStatus } from "@/components/EdgeMeter";
import styles from "./page.module.scss";

// Static placeholder signals — design-system showcase only. No data wiring yet
// (BFF / Zod / SSE are a later slice). Shapes loosely mirror quant Signal models.
type DemoSignal = {
  id: string;
  question: string;
  side: string;
  strategy: string;
  edgeBps: number;
  gateBps: number;
  sizePct: number;
  status: EdgeStatus;
  glow: "violet" | "magenta" | "cyan" | "green";
};

const SIGNALS: DemoSignal[] = [
  {
    id: "1",
    question: "Will the Fed cut rates at the July 2026 meeting?",
    side: "BUY YES",
    strategy: "extreme_correction",
    edgeBps: 184,
    gateBps: 70,
    sizePct: 4.2,
    status: "pass",
    glow: "green",
  },
  {
    id: "2",
    question: "Set-arb: 2026 World Cup group A outright (4 outcomes)",
    side: "LONG SET",
    strategy: "set_arbitrage",
    edgeBps: 96,
    gateBps: 60,
    sizePct: 2.8,
    status: "pass",
    glow: "violet",
  },
  {
    id: "3",
    question: "Will BTC close above $150k on 2026-12-31?",
    side: "BUY NO",
    strategy: "favourite_longshot",
    edgeBps: 42,
    gateBps: 65,
    sizePct: 0,
    status: "watch",
    glow: "cyan",
  },
  {
    id: "4",
    question: "Will the incumbent win the 2026 governor race?",
    side: "BUY YES",
    strategy: "favourite_longshot",
    edgeBps: -18,
    gateBps: 55,
    sizePct: 0,
    status: "gated",
    glow: "magenta",
  },
];

const STATUS_BADGE: Record<EdgeStatus, { variant: BadgeVariant; label: string }> = {
  pass: { variant: "pass", label: "Gate ✓ pass" },
  watch: { variant: "watch", label: "Below gate" },
  gated: { variant: "gated", label: "Gated" },
};

const STATS = [
  { label: "Live signals", value: "12", unit: "tracked" },
  { label: "Clearing gate", value: "5", unit: "actionable" },
  { label: "Best net edge", value: "+184", unit: "bps" },
  { label: "Bankroll at risk", value: "9.8", unit: "%" },
];

export default function DashboardPage() {
  return (
    <>
      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>
            <span className="mono">POLYMARKET</span> · edge scanner
          </p>
          <h1 className={styles.title}>
            Quantitative edges,
            <br />
            <span className={styles.titleAccent}>surfaced for you.</span>
          </h1>
          <p className={styles.lede}>
            EdgeOracle ranks live mispricings by net edge after costs. It advises —
            you decide. Every bet is gated on the CI lower bound clearing fees,
            slippage and model error.
          </p>
        </div>

        <GlassCard strong glow="violet" className={styles.statsCard}>
          <div className={styles.stats}>
            {STATS.map((s) => (
              <div key={s.label} className={styles.stat}>
                <span className={`${styles.statValue} mono`}>{s.value}</span>
                <span className={styles.statUnit}>{s.unit}</span>
                <span className={styles.statLabel}>{s.label}</span>
              </div>
            ))}
          </div>
        </GlassCard>
      </section>

      <section aria-labelledby="signals-heading" className={styles.signals}>
        <div className={styles.sectionHead}>
          <h2 id="signals-heading" className={styles.sectionTitle}>
            Ranked edge list
          </h2>
          <Badge dot pulse variant="accent">
            Streaming
          </Badge>
        </div>

        <ul className={styles.grid}>
          {SIGNALS.map((sig) => {
            const badge = STATUS_BADGE[sig.status];
            return (
              <GlassCard
                key={sig.id}
                as="li"
                interactive
                glow={sig.glow}
                className={styles.signalCard}
              >
                <div className={styles.signalTop}>
                  <Badge variant="neutral">{sig.side}</Badge>
                  <Badge variant={badge.variant}>{badge.label}</Badge>
                </div>

                <h3 className={styles.question}>{sig.question}</h3>

                <EdgeMeter edgeBps={sig.edgeBps} thresholdBps={sig.gateBps} />

                <div className={styles.signalFoot}>
                  <span className={styles.strategy}>
                    {sig.strategy.replace(/_/g, " ")}
                  </span>
                  <span className={styles.size}>
                    size{" "}
                    <span className="mono">
                      {sig.sizePct > 0 ? `${sig.sizePct.toFixed(1)}%` : "—"}
                    </span>
                  </span>
                </div>
              </GlassCard>
            );
          })}
        </ul>
      </section>
    </>
  );
}
