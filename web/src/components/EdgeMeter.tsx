import styles from "./EdgeMeter.module.scss";

export type EdgeStatus = "pass" | "watch" | "gated";

export type EdgeMeterModel = {
  fillPct: number;
  thresholdPct: number;
  status: EdgeStatus;
  domainMax: number;
};

/**
 * Pure geometry/status for the meter — kept separate so it can be unit-tested
 * without rendering. `edgeBps` is the net edge; `thresholdBps` is the gate the
 * edge must clear (the CI-lower-bound gate, per the money-math rules).
 */
export function edgeMeterModel(
  edgeBps: number,
  thresholdBps: number,
  max?: number,
): EdgeMeterModel {
  const domainMax = Math.max(
    max ?? Math.max(thresholdBps * 2, edgeBps * 1.25),
    1,
  );
  const clamp01 = (n: number) => Math.min(Math.max(n, 0), 1);
  const status: EdgeStatus =
    edgeBps >= thresholdBps ? "pass" : edgeBps >= 0 ? "watch" : "gated";

  return {
    fillPct: clamp01(edgeBps / domainMax) * 100,
    thresholdPct: clamp01(thresholdBps / domainMax) * 100,
    status,
    domainMax,
  };
}

type EdgeMeterProps = {
  edgeBps: number;
  thresholdBps: number;
  max?: number;
  label?: string;
};

const STATUS_LABEL: Record<EdgeStatus, string> = {
  pass: "Clears gate",
  watch: "Below gate",
  gated: "Negative",
};

/** Horizontal neon meter: edge magnitude vs the gate threshold marker. */
export default function EdgeMeter({
  edgeBps,
  thresholdBps,
  max,
  label = "Net edge",
}: EdgeMeterProps) {
  const { fillPct, thresholdPct, status, domainMax } = edgeMeterModel(
    edgeBps,
    thresholdBps,
    max,
  );
  const sign = edgeBps > 0 ? "+" : "";
  const valueText = `${sign}${edgeBps} bps, gate ${thresholdBps} bps — ${STATUS_LABEL[status]}`;

  return (
    <div className={`${styles.meter} ${styles[status]}`}>
      <div className={styles.head}>
        <span className={styles.label}>{label}</span>
        <span className={`${styles.value} mono`}>
          {sign}
          {edgeBps}
          <span className={styles.unit}>bps</span>
        </span>
      </div>

      <div
        className={styles.track}
        role="meter"
        aria-label={label}
        aria-valuenow={edgeBps}
        aria-valuemin={0}
        aria-valuemax={domainMax}
        aria-valuetext={valueText}
      >
        <div className={styles.fill} style={{ width: `${fillPct}%` }} />
        <div
          className={styles.threshold}
          style={{ left: `${thresholdPct}%` }}
          aria-hidden="true"
        >
          <span className={styles.thresholdLabel}>gate</span>
        </div>
      </div>
    </div>
  );
}
