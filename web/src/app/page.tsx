"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import GlassCard from "@/components/GlassCard";
import Badge from "@/components/Badge";
import EdgeMeter from "@/components/EdgeMeter";
import { AdvisedSignalListSchema, AdvisedSignalSchema, type AdvisedSignal } from "@/lib/schemas/signal";
import { UserConfigSchema, type UserConfig } from "@/lib/schemas/config";
import { mergeSignal } from "@/lib/stream";
import {
  advisedEv,
  advisedLossProb,
  edgeToBps,
  fmtPct,
  fmtUsd,
  fmtUsdSigned,
  sideLabel,
  signalStatus,
  strategyLabel,
} from "@/lib/format";
import styles from "./page.module.scss";

const GLOWS = ["violet", "green", "cyan", "magenta"] as const;
const STATUS_BADGE = {
  pass: { variant: "pass", label: "Gate ✓ pass" },
  watch: { variant: "watch", label: "Below gate" },
  gated: { variant: "gated", label: "Gated" },
} as const;

// "Your best bets right now": the safest live opportunities (risk-free arb first, then
// gate-passing directional), sized to the personal bankroll, with real expected-$ and risk.
export default function DashboardPage() {
  const [signals, setSignals] = useState<AdvisedSignal[]>([]);
  const [config, setConfig] = useState<UserConfig | null>(null);
  const [live, setLive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastSeen = useRef(new Map<string, AdvisedSignal>());

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [sigRes, cfgRes] = await Promise.all([
          fetch("/api/signals?safe_only=true&sort=safety", { headers: { accept: "application/json" } }),
          fetch("/api/config", { headers: { accept: "application/json" } }),
        ]);
        if (!sigRes.ok) throw new Error(`signals HTTP ${sigRes.status}`);
        if (!cfgRes.ok) throw new Error(`config HTTP ${cfgRes.status}`);
        const parsed = AdvisedSignalListSchema.parse(await sigRes.json());
        const cfg = UserConfigSchema.parse(await cfgRes.json());
        if (!cancelled) {
          lastSeen.current = new Map(parsed.map((s) => [s.id, s]));
          setSignals(parsed);
          setConfig(cfg);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unknown error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Live updates: merge the conflated SSE stream in place (keeps the "best bets" fresh).
  useEffect(() => {
    const source = new EventSource("/api/stream");
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false);
    source.onmessage = (event) => {
      const parsed = AdvisedSignalSchema.safeParse(JSON.parse(event.data));
      if (!parsed.success) return;
      setSignals((prev) => mergeSignal(prev, parsed.data));
    };
    return () => {
      source.close();
      setLive(false);
    };
  }, []);

  const stats = useMemo(() => {
    const clearing = signals.filter((s) => s.gate_passed).length;
    const bestNet = signals.reduce((m, s) => Math.max(m, s.net_edge), 0);
    const atRisk = signals.reduce((sum, s) => sum + s.recommended_size_usd, 0);
    const bankroll = config?.bankroll ?? 0;
    const atRiskPct = bankroll > 0 ? (atRisk / bankroll) * 100 : 0;
    return [
      { label: "Safe signals", value: String(signals.length), unit: "live" },
      { label: "Clearing gate", value: String(clearing), unit: "actionable" },
      { label: "Best net edge", value: `+${edgeToBps(bestNet)}`, unit: "bps" },
      { label: "Bankroll at risk", value: atRiskPct.toFixed(1), unit: "%" },
    ];
  }, [signals, config]);

  const top = signals.slice(0, 6);

  return (
    <>
      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>
            <span className="mono">POLYMARKET</span> · edge scanner
          </p>
          <h1 className={styles.title}>
            Your best bets,
            <br />
            <span className={styles.titleAccent}>safest first.</span>
          </h1>
          <p className={styles.lede}>
            Risk-free arbitrage leads, then positive-EV bets with the biggest margin over cost —
            each sized to your bankroll{config ? ` (${fmtUsd(config.bankroll)})` : ""}. Only set
            arbitrage is truly risk-free; the rest win on average, not every time. Tune sizing in{" "}
            <Link href="/settings" className={styles.inlineLink}>
              settings
            </Link>
            .
          </p>
        </div>

        <GlassCard strong glow="violet" className={styles.statsCard}>
          <div className={styles.stats}>
            {stats.map((s) => (
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
            Best bets right now
          </h2>
          {live ? (
            <Badge dot pulse variant="accent">
              streaming · live
            </Badge>
          ) : (
            <Badge dot variant="neutral">
              REST · reconnecting…
            </Badge>
          )}
        </div>

        {error ? (
          <GlassCard strong glow="magenta" className={styles.signalCard}>
            <strong>Couldn’t reach the quant service.</strong>
            <span>{error}</span>
          </GlassCard>
        ) : top.length === 0 ? (
          <GlassCard strong className={styles.signalCard}>
            No safe signals right now. The scanner records opportunities as markets dislocate.
          </GlassCard>
        ) : (
          <ul className={styles.grid}>
            {top.map((sig, i) => {
              const status = signalStatus(sig);
              const badge = STATUS_BADGE[status];
              const ev = advisedEv(sig);
              const loss = advisedLossProb(sig);
              const arb = sig.strategy === "set_arb";
              return (
                <GlassCard
                  key={sig.id}
                  as="li"
                  interactive
                  glow={GLOWS[i % GLOWS.length]}
                  className={styles.signalCard}
                >
                  <Link href={`/signals/${encodeURIComponent(sig.id)}`} className={styles.cardLink}>
                    <div className={styles.signalTop}>
                      <Badge variant="neutral">{sideLabel(sig.kind)}</Badge>
                      <Badge variant={badge.variant}>{badge.label}</Badge>
                    </div>

                    <h3 className={styles.question}>
                      {sig.market_question ?? sig.condition_id}
                    </h3>

                    <EdgeMeter edgeBps={edgeToBps(sig.net_edge)} thresholdBps={0} label="net" />

                    <div className={styles.cardEcon}>
                      <span className={styles.econItem}>
                        <span className={styles.econLabel}>{arb ? "Locked" : "Expected"}</span>
                        <span className="mono">
                          {ev === null ? "—" : arb ? fmtUsd(ev) : fmtUsdSigned(ev)}
                        </span>
                      </span>
                      <span className={styles.econItem}>
                        <span className={styles.econLabel}>Loss risk</span>
                        <span className="mono">
                          {arb ? "risk-free" : loss === null ? "—" : fmtPct(loss)}
                        </span>
                      </span>
                    </div>

                    <div className={styles.signalFoot}>
                      <span className={styles.strategy}>{strategyLabel(sig.strategy)}</span>
                      <span className={styles.size}>
                        size{" "}
                        <span className="mono">
                          {sig.recommended_size_usd > 0 ? fmtUsd(sig.recommended_size_usd) : "—"}
                        </span>
                      </span>
                    </div>
                  </Link>
                </GlassCard>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}
