"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import GlassCard from "@/components/GlassCard";
import Badge from "@/components/Badge";
import { StrategySchema } from "@/lib/schemas/signal";
import { PositionSideSchema, type OpenPositionRequest } from "@/lib/schemas/position";
import styles from "./page.module.scss";

const EMPTY = {
  market_id: "",
  condition_id: "",
  strategy: "extreme_correction",
  side: "yes",
  entry_price: "",
  stake_usd: "",
};

// Record a bet you placed on Polymarket. Minimal form — the heavy lifting (shares, P&L) is the
// server's. A "Track this bet" deep-link from a signal prefills these fields via URL query params.
export default function RecordBetForm({ onRecorded }: { onRecorded: () => void }) {
  const params = useSearchParams();
  const prefill = {
    market_id: params.get("market_id") ?? "",
    condition_id: params.get("condition_id") ?? "",
    strategy: params.get("strategy") ?? "extreme_correction",
    side: params.get("side") ?? "yes",
    entry_price: params.get("entry_price") ?? "",
    stake_usd: params.get("stake_usd") ?? "",
  };
  const hasPrefill = Boolean(params.get("market_id") || params.get("condition_id"));
  const [open, setOpen] = useState(hasPrefill);
  const [form, setForm] = useState(hasPrefill ? prefill : EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (k: keyof typeof EMPTY, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const body: OpenPositionRequest = {
        market_id: form.market_id,
        condition_id: form.condition_id,
        strategy: StrategySchema.parse(form.strategy),
        side: PositionSideSchema.parse(form.side),
        entry_price: Number(form.entry_price),
        stake_usd: Number(form.stake_usd),
      };
      const res = await fetch("/api/positions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setForm(EMPTY);
      setOpen(false);
      onRecorded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to record");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className={styles.recordBtn} onClick={() => setOpen(true)}>
        + Record a bet
      </button>
    );
  }

  return (
    <GlassCard strong className={styles.formCard}>
      <h2 className={styles.formTitle}>Record a placed bet</h2>
      <div className={styles.formGrid}>
        <Field label="Market id" value={form.market_id} onChange={(v) => set("market_id", v)} />
        <Field label="Condition id" value={form.condition_id} onChange={(v) => set("condition_id", v)} />
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Strategy</span>
          <select className={styles.input} value={form.strategy} onChange={(e) => set("strategy", e.target.value)}>
            {StrategySchema.options.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Side</span>
          <select className={styles.input} value={form.side} onChange={(e) => set("side", e.target.value)}>
            {PositionSideSchema.options.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <Field label="Entry price (0–1)" value={form.entry_price} onChange={(v) => set("entry_price", v)} type="number" />
        <Field label="Stake (USD)" value={form.stake_usd} onChange={(v) => set("stake_usd", v)} type="number" />
      </div>
      <div className={styles.formActions}>
        <button type="button" className={styles.save} onClick={submit} disabled={busy}>
          {busy ? "Saving…" : "Record"}
        </button>
        <button type="button" className={styles.cancel} onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </button>
        {error && <Badge variant="gated">{error}</Badge>}
      </div>
    </GlassCard>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <label className={styles.field}>
      <span className={styles.fieldLabel}>{label}</span>
      <input
        type={type}
        className={styles.input}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
