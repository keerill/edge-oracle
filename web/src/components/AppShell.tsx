import type { ReactNode } from "react";
import Badge from "./Badge";
import ThemeToggle from "./ThemeToggle";
import styles from "./AppShell.module.scss";

const NAV = [
  { label: "Signals", active: true },
  { label: "Markets", active: false },
  { label: "Backtest", active: false },
  { label: "Calibration", active: false },
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
            {NAV.map((item) => (
              <a
                key={item.label}
                href="#"
                className={`${styles.navLink} ${item.active ? styles.navActive : ""}`}
                aria-current={item.active ? "page" : undefined}
              >
                {item.label}
              </a>
            ))}
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
