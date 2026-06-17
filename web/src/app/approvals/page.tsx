"use client";

import { useCallback, useEffect, useState } from "react";
import GlassCard from "@/components/GlassCard";
import Badge from "@/components/Badge";
import { PendingIntentListSchema, type PendingIntent } from "@/lib/schemas/exec";
import { fmtPrice, fmtUsd } from "@/lib/format";
import styles from "./page.module.scss";

// Phase 6-UI: the human-approval surface. Lists the executor's pending-approval intents (proposed
// automatically from advisor signals) and approves them on a click — which signs + dry-run-submits.
// Nothing reaches a network while the executor runs in dry-run.
export default function ApprovalsPage() {
  const [intents, setIntents] = useState<PendingIntent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ id: string; text: string; ok: boolean } | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/exec/pending", { headers: { accept: "application/json" } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setIntents(PendingIntentListSchema.parse(await res.json()));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const approve = async (id: string) => {
    setBusy(id);
    setNote(null);
    try {
      const res = await fetch(`/api/exec/approve/${encodeURIComponent(id)}`, { method: "POST" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
      setNote({ id, text: `Status: ${body.status} (dry-run — nothing sent)`, ok: body.status === "submitted" });
      await load();
    } catch (err) {
      setNote({ id, text: err instanceof Error ? err.message : "approve failed", ok: false });
    } finally {
      setBusy(null);
    }
  };

  return (
    <section aria-labelledby="approvals-heading" className={styles.wrap}>
      <div className={styles.head}>
        <div>
          <p className={styles.eyebrow}>
            <span className="mono">EXECUTOR</span> · semi-auto
          </p>
          <h1 id="approvals-heading" className={styles.title}>
            Approvals
          </h1>
        </div>
        <Badge variant="watch" dot>
          dry-run
        </Badge>
      </div>

      <p className={styles.lede}>
        Intents proposed automatically from advisor signals, awaiting your decision. Approving signs
        the order and records what <em>would</em> be sent — nothing reaches a network while the
        executor runs in dry-run. Every trade is manual.
      </p>

      {error ? (
        <GlassCard strong glow="magenta" className={styles.notice}>
          Couldn’t reach the executor: {error}
        </GlassCard>
      ) : intents === null ? (
        <GlassCard strong className={styles.notice}>
          Loading pending intents…
        </GlassCard>
      ) : intents.length === 0 ? (
        <GlassCard strong className={styles.notice}>
          No intents awaiting approval. The consumer proposes them as actionable signals arrive.
        </GlassCard>
      ) : (
        <ul className={styles.list}>
          {intents.map((it) => (
            <GlassCard key={it.intent_id} as="li" strong className={styles.card}>
              <div className={styles.cardHead}>
                <Badge variant="neutral">{it.side.replace(/_/g, " ").toUpperCase()}</Badge>
                <span className={styles.market}>{it.condition_id}</span>
              </div>
              <dl className={styles.facts}>
                <Fact label="Size" value={`${it.size}`} />
                <Fact label="Limit price" value={it.max_price === null ? "—" : fmtPrice(it.max_price)} />
                <Fact label="Notional" value={fmtUsd(it.notional_usd)} />
                <Fact label="From signal" value={it.source_signal_id} mono />
              </dl>
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.approve}
                  onClick={() => approve(it.intent_id)}
                  disabled={busy === it.intent_id}
                >
                  {busy === it.intent_id ? "Approving…" : "Approve (dry-run)"}
                </button>
                {note?.id === it.intent_id && (
                  <Badge variant={note.ok ? "pass" : "gated"}>{note.text}</Badge>
                )}
              </div>
            </GlassCard>
          ))}
        </ul>
      )}
    </section>
  );
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className={styles.fact}>
      <dt className={styles.factLabel}>{label}</dt>
      <dd className={`${styles.factValue} ${mono ? "mono" : ""}`}>{value}</dd>
    </div>
  );
}
