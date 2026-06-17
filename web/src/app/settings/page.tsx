"use client";

import { useEffect, useState } from "react";
import GlassCard from "@/components/GlassCard";
import Badge from "@/components/Badge";
import { useConfig } from "@/lib/useConfig";
import { type UserConfig } from "@/lib/schemas/config";
import { fmtPct, fmtUsd } from "@/lib/format";
import styles from "./page.module.scss";

// Personal sizing / risk knobs. Sliders bind to /api/config (GET/PUT); saving re-sizes every
// signal on the next fetch. Bankroll is a number input (open range); the rest are fractions.
export default function SettingsPage() {
  const { config, status, error, save } = useConfig();
  const [draft, setDraft] = useState<UserConfig | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (config) setDraft(config);
  }, [config]);

  if (!draft) {
    return (
      <GlassCard strong className={styles.notice}>
        {status === "error" ? `Couldn’t load config: ${error}` : "Loading config…"}
      </GlassCard>
    );
  }

  const set = (key: keyof UserConfig, value: number) => {
    setDraft({ ...draft, [key]: value });
    setSaved(false);
  };

  const onSave = async () => {
    const ok = await save(draft);
    setSaved(ok);
  };

  return (
    <section aria-labelledby="settings-heading" className={styles.wrap}>
      <div className={styles.head}>
        <div>
          <p className={styles.eyebrow}>
            <span className="mono">PERSONAL</span> · sizing &amp; risk
          </p>
          <h1 id="settings-heading" className={styles.title}>
            Settings
          </h1>
        </div>
      </div>

      <p className={styles.lede}>
        These knobs personalize every recommendation. Smaller fractional Kelly and a tighter cap
        trade upside for survivability — the conservative defaults minimize the chance of a big
        drawdown. Risk threshold filters how much loss probability you’ll tolerate per bet.
      </p>

      <GlassCard strong className={styles.card}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>
            Bankroll <span className="mono">{fmtUsd(draft.bankroll)}</span>
          </span>
          <input
            type="number"
            min={0}
            step={50}
            value={draft.bankroll}
            onChange={(e) => set("bankroll", Number(e.target.value))}
            className={styles.number}
            aria-label="Bankroll in USD"
          />
        </label>

        <Slider
          label="Fractional Kelly"
          help="Fraction of full Kelly applied to every bet (lower = safer)."
          value={draft.kelly_frac}
          onChange={(v) => set("kelly_frac", v)}
        />
        <Slider
          label="Per-position cap"
          help="Hard ceiling on any single bet as a fraction of bankroll."
          value={draft.kelly_cap}
          onChange={(v) => set("kelly_cap", v)}
        />
        <Slider
          label="Per-theme correlation cap"
          help="Max combined exposure to one macro theme (fraction of bankroll)."
          value={draft.corr_cap_frac}
          onChange={(v) => set("corr_cap_frac", v)}
        />
        <Slider
          label="Max loss probability"
          help="Filter out bets whose chance of losing exceeds this."
          value={draft.risk_threshold}
          onChange={(v) => set("risk_threshold", v)}
        />

        <div className={styles.actions}>
          <button
            type="button"
            className={styles.save}
            onClick={onSave}
            disabled={status === "saving"}
          >
            {status === "saving" ? "Saving…" : "Save"}
          </button>
          {saved && <Badge variant="pass">Saved ✓</Badge>}
          {status === "error" && <Badge variant="gated">{error}</Badge>}
        </div>
      </GlassCard>
    </section>
  );
}

function Slider({
  label,
  help,
  value,
  onChange,
}: {
  label: string;
  help: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className={styles.field}>
      <span className={styles.fieldLabel}>
        {label} <span className="mono">{fmtPct(value)}</span>
      </span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className={styles.range}
        aria-label={label}
      />
      <span className={styles.help}>{help}</span>
    </label>
  );
}
