"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import GlassCard from "@/components/GlassCard";
import Badge from "@/components/Badge";
import {
  PositionsResponseSchema,
  type PositionsResponse,
  type PositionWithPnl,
} from "@/lib/schemas/position";
import { fmtPrice, fmtUsd, fmtUsdSigned } from "@/lib/format";
import RecordBetForm from "./RecordBetForm";
import styles from "./page.module.scss";

// The portfolio: bets you've placed (manually on Polymarket), with live unrealized P&L for open
// positions and realized P&L for closed ones, plus running totals. Settlement is automatic when
// the market resolves (the resolution-watcher closes open positions).
export default function PortfolioPage() {
  const [data, setData] = useState<PositionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/positions", { headers: { accept: "application/json" } });
      if (!res.ok) throw new Error(`positions HTTP ${res.status}`);
      setData(PositionsResponseSchema.parse(await res.json()));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section aria-labelledby="portfolio-heading" className={styles.wrap}>
      <div className={styles.head}>
        <div>
          <p className={styles.eyebrow}>
            <span className="mono">PERSONAL</span> · placed bets
          </p>
          <h1 id="portfolio-heading" className={styles.title}>
            Portfolio
          </h1>
        </div>
      </div>

      {data && (
        <GlassCard strong glow="violet" className={styles.totals}>
          <Total label="Open exposure" value={fmtUsd(data.total_exposure)} />
          <Total label="Unrealized P&L" value={fmtUsdSigned(data.total_unrealized_pnl)} signed={data.total_unrealized_pnl} />
          <Total label="Realized P&L" value={fmtUsdSigned(data.total_realized_pnl)} signed={data.total_realized_pnl} />
        </GlassCard>
      )}

      <Suspense fallback={null}>
        <RecordBetForm onRecorded={load} />
      </Suspense>

      {error ? (
        <GlassCard strong glow="magenta" className={styles.notice}>
          Couldn’t load positions: {error}
        </GlassCard>
      ) : !data ? (
        <GlassCard strong className={styles.notice}>
          Loading positions…
        </GlassCard>
      ) : data.positions.length === 0 ? (
        <GlassCard strong className={styles.notice}>
          No positions yet. Record a bet you placed to track its P&L here.
        </GlassCard>
      ) : (
        <GlassCard strong className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="sr-only">Your positions with live and realized P&L.</caption>
            <thead>
              <tr>
                <th scope="col">Market</th>
                <th scope="col" className={styles.num}>Side</th>
                <th scope="col" className={styles.num}>Entry</th>
                <th scope="col" className={styles.num}>Stake</th>
                <th scope="col" className={styles.num}>P&L</th>
                <th scope="col" className={styles.num}>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.positions.map((p) => (
                <PositionRow key={p.position.id} row={p} />
              ))}
            </tbody>
          </table>
        </GlassCard>
      )}
    </section>
  );
}

function PositionRow({ row }: { row: PositionWithPnl }) {
  const p = row.position;
  const closed = p.status === "closed";
  const pnl = closed ? p.pnl : row.unrealized_pnl;
  return (
    <tr>
      <td className={styles.market}>
        <span className={styles.condition}>{p.condition_id}</span>
        <span className={styles.strategy}>{p.strategy}</span>
      </td>
      <td className={`${styles.num} mono`}>{p.side.toUpperCase()}</td>
      <td className={`${styles.num} mono`}>{fmtPrice(p.entry_price)}</td>
      <td className={`${styles.num} mono`}>{fmtUsd(p.stake_usd)}</td>
      <td className={`${styles.num} mono`}>
        {pnl === null ? "—" : fmtUsdSigned(pnl)}
        {!closed && row.unrealized_pnl !== null && <span className={styles.tag}>unrealized</span>}
      </td>
      <td className={styles.num}>
        <Badge variant={closed ? "neutral" : "accent"}>{closed ? "closed" : "open"}</Badge>
      </td>
    </tr>
  );
}

function Total({ label, value, signed }: { label: string; value: string; signed?: number }) {
  const cls =
    signed === undefined ? "" : signed > 0 ? styles.pos : signed < 0 ? styles.neg : "";
  return (
    <div className={styles.total}>
      <span className={styles.totalLabel}>{label}</span>
      <span className={`${styles.totalValue} mono ${cls}`}>{value}</span>
    </div>
  );
}
