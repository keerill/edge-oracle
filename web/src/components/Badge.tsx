import type { ReactNode } from "react";
import styles from "./Badge.module.scss";

export type BadgeVariant =
  | "neutral"
  | "accent"
  | "pass"
  | "watch"
  | "gated";

type BadgeProps = {
  children: ReactNode;
  variant?: BadgeVariant;
  /** Leading status dot (optionally pulsing for "live"). */
  dot?: boolean;
  pulse?: boolean;
  className?: string;
};

/** Pill / badge. Gate states (pass/watch/gated) are colour + label coded. */
export default function Badge({
  children,
  variant = "neutral",
  dot = false,
  pulse = false,
  className,
}: BadgeProps) {
  const classes = [styles.badge, styles[variant], className ?? ""]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={classes}>
      {dot && (
        <span
          className={`${styles.dot} ${pulse ? styles.pulse : ""}`}
          aria-hidden="true"
        />
      )}
      {children}
    </span>
  );
}
