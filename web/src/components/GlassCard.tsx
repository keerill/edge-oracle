import type { ElementType, ReactNode } from "react";
import styles from "./GlassCard.module.scss";

type AccentGlow = "violet" | "magenta" | "cyan" | "green" | "amber" | "red" | "none";

type GlassCardProps = {
  children: ReactNode;
  /** Render as a different element (e.g. "section", "article", "li"). */
  as?: ElementType;
  /** Heavier, more opaque surface for primary panels. */
  strong?: boolean;
  /** Lift + glow on hover (use for clickable cards). */
  interactive?: boolean;
  /** Edge-glow accent colour. */
  glow?: AccentGlow;
  className?: string;
};

/** The core frosted-glass surface primitive every panel is built from. */
export default function GlassCard({
  children,
  as: Tag = "div",
  strong = false,
  interactive = false,
  glow = "none",
  className,
}: GlassCardProps) {
  const classes = [
    styles.card,
    strong ? styles.strong : "",
    interactive ? styles.interactive : "",
    glow !== "none" ? styles[`glow-${glow}`] : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  return <Tag className={classes}>{children}</Tag>;
}
