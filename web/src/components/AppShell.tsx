import type { ReactNode } from "react";
import Link from "next/link";
import Badge from "./Badge";
import ThemeToggle from "./ThemeToggle";
import styles from "./AppShell.module.scss";

// `href: null` = not yet routed.
const NAV: { label: string; href: string | null }[] = [
  { label: "Signals", href: "/signals" },
  { label: "Portfolio", href: "/portfolio" },
  { label: "Backtest", href: "/backtest" },
  { label: "Calibration", href: "/calibration" },
  { label: "Settings", href: "/settings" },
];

/** Top bar + centered content frame. The persistent chrome around every page. */
export default function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <div className={styles.inner}>
          <a className={styles.brand} href="/" aria-label="EdgeOracle home">
            <span className={styles.mark} aria-hidden="true" />
            <span className={styles.wordmark}>
              Edge<span className={styles.wordmarkAccent}>Oracle</span>
            </span>
            <span className={styles.tagline}>quant advisor</span>
          </a>

          <nav className={styles.nav} aria-label="Primary">
            {NAV.map((item) =>
              item.href ? (
                <Link key={item.label} href={item.href} className={styles.navLink}>
                  {item.label}
                </Link>
              ) : (
                <span
                  key={item.label}
                  className={`${styles.navLink} ${styles.navDisabled}`}
                  aria-disabled="true"
                >
                  {item.label}
                </span>
              ),
            )}
          </nav>

          <div className={styles.actions}>
            <Badge variant="pass" dot pulse>
              Live
            </Badge>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className={styles.main}>
        <div className={styles.content}>{children}</div>
      </main>
    </div>
  );
}
