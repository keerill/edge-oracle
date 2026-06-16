"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import Badge from "@/components/Badge";
import EdgeMeter from "@/components/EdgeMeter";
import GlassCard from "@/components/GlassCard";
import type { AdvisedSignal } from "@/lib/schemas/signal";
import {
  edgeToBps,
  fmtPct,
  fmtPrice,
  fmtUsd,
  sideLabel,
  signalStatus,
  strategyLabel,
} from "@/lib/format";
import styles from "./SignalsTable.module.scss";

type SortKey = "market_price" | "p" | "recommended_size_usd" | "net_edge";
type SortDir = "asc" | "desc";

const STATUS_LABEL = { pass: "Gate ✓", watch: "Below gate", gated: "Gated" } as const;

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "market_price", label: "Price (m)" },
  { key: "p", label: "Your p" },
  { key: "recommended_size_usd", label: "Size" },
  { key: "net_edge", label: "Net edge" },
];

// Numeric sort accessor — `p` is null for arb/longshot; sort those to the bottom (desc).
function sortValue(signal: AdvisedSignal, key: SortKey): number {
  const v = signal[key];
  return v === null ? Number.NEGATIVE_INFINITY : v;
}

export default function SignalsTable({ signals }: { signals: AdvisedSignal[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("net_edge");
  const [dir, setDir] = useState<SortDir>("desc");

  const sorted = useMemo(() => {
    const rows = [...signals];
    rows.sort((a, b) => {
      const av = sortValue(a, sortKey);
      const bv = sortValue(b, sortKey);
      return dir === "desc" ? bv - av : av - bv;
    });
    return rows;
  }, [signals, sortKey, dir]);

  function toggle(key: SortKey) {
    if (key === sortKey) setDir((d) => (d === "desc" ? "asc" : "desc"));
    else {
      setSortKey(key);
      setDir("desc");
    }
  }

  if (signals.length === 0) {
    return (
      <GlassCard strong className={styles.empty}>
        No open signals right now. The scanner records opportunities as markets dislocate.
      </GlassCard>
    );
  }

  return (
    <GlassCard strong className={styles.wrap}>
      <table className={styles.table}>
        <caption className="sr-only">
          Open signals, sortable by price, confidence, recommended size, and net-of-cost edge.
        </caption>
        <thead>
          <tr>
            <th scope="col" className={styles.marketCol}>
              Market
            </th>
            {COLUMNS.map((col) => {
              const active = col.key === sortKey;
              return (
                <th
                  key={col.key}
                  scope="col"
                  aria-sort={active ? (dir === "desc" ? "descending" : "ascending") : "none"}
                  className={styles.numCol}
                >
                  <button
                    type="button"
                    className={`${styles.sortBtn} ${active ? styles.sortActive : ""}`}
                    onClick={() => toggle(col.key)}
                  >
                    {col.label}
                    <span aria-hidden="true" className={styles.caret}>
                      {active ? (dir === "desc" ? "▾" : "▴") : "⋅"}
                    </span>
                  </button>
                </th>
              );
            })}
            <th scope="col" className={styles.gateCol}>
              Gate
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => {
            const status = signalStatus(s);
            return (
              <tr key={s.id} className={styles.row}>
                <td className={styles.marketCell}>
                  <Link href={`/signals/${encodeURIComponent(s.id)}`} className={styles.marketLink}>
                    <span className={styles.question}>
                      {s.market_question ?? s.condition_id}
                    </span>
                    <span className={styles.sub}>
                      <Badge variant="neutral">{sideLabel(s.kind)}</Badge>
                      <span className={styles.strategy}>{strategyLabel(s.strategy)}</span>
                    </span>
                  </Link>
                </td>
                <td className={`${styles.num} mono`}>{fmtPrice(s.market_price)}</td>
                <td className={`${styles.num} mono`}>{s.p === null ? "—" : fmtPrice(s.p)}</td>
                <td className={`${styles.num} mono`}>
                  {s.recommended_size_usd > 0 ? (
                    <>
                      {fmtUsd(s.recommended_size_usd)}
                      <span className={styles.sizePct}>{fmtPct(s.recommended_size_pct)}</span>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className={styles.meterCell}>
                  <EdgeMeter edgeBps={edgeToBps(s.net_edge)} thresholdBps={0} label="net" />
                </td>
                <td className={styles.gateCell}>
                  <Badge variant={status}>{STATUS_LABEL[status]}</Badge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </GlassCard>
  );
}
