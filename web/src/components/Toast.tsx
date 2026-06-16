"use client";

import GlassCard from "./GlassCard";
import Badge, { type BadgeVariant } from "./Badge";
import styles from "./Toast.module.scss";

// Opportunity toasts use "success"; system alerts map their severity straight through.
export type ToastSeverity = "info" | "success" | "warning" | "error";

type GlassGlow = "cyan" | "green" | "amber" | "red";

// Severity -> existing neon design tokens (GlassCard glow + Badge tint). No new colours.
const SEVERITY: Record<ToastSeverity, { glow: GlassGlow; badge: BadgeVariant; label: string }> = {
  info: { glow: "cyan", badge: "accent", label: "Info" },
  success: { glow: "green", badge: "pass", label: "Signal" },
  warning: { glow: "amber", badge: "watch", label: "Warning" },
  error: { glow: "red", badge: "gated", label: "Error" },
};

export type ToastProps = {
  severity: ToastSeverity;
  title: string;
  detail?: string;
  onDismiss?: () => void;
};

/** A single toast: a glass card tinted by severity, its own ARIA live region. */
export default function Toast({ severity, title, detail, onDismiss }: ToastProps) {
  const s = SEVERITY[severity];
  const isError = severity === "error";
  return (
    // Each toast is its own live region: errors interrupt (assertive), the rest are polite.
    <div role={isError ? "alert" : "status"} aria-live={isError ? "assertive" : "polite"}>
      <GlassCard strong glow={s.glow} className={styles.toast}>
        <div className={styles.header}>
          <Badge variant={s.badge}>{s.label}</Badge>
          <span className={styles.title}>{title}</span>
          {onDismiss && (
            <button
              type="button"
              className={styles.dismiss}
              aria-label="Dismiss"
              onClick={onDismiss}
            >
              ×
            </button>
          )}
        </div>
        {detail && <p className={styles.detail}>{detail}</p>}
      </GlassCard>
    </div>
  );
}
