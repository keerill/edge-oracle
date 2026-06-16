"use client";

import { linearScale, plotArea, type ChartDims } from "@/lib/charts";
import { fmtPct } from "@/lib/format";
import styles from "./ReliabilityChart.module.scss";

// Claimed-vs-realized reliability curve. A perfectly calibrated model sits on the y=x
// diagonal; points below it are overconfident (claimed > realized). Dot size ∝ sample count.

export type ReliabilityBin = {
  lo: number;
  hi: number;
  count: number;
  claimed: number | null;
  realized: number | null;
};

export type ReliabilityPoint = {
  cx: number;
  cy: number;
  r: number;
  claimed: number;
  realized: number;
  count: number;
};

export type ReliabilityModel = {
  points: ReliabilityPoint[];
  diagonal: { x1: number; y1: number; x2: number; y2: number };
  ticks: number[];
  dims: ChartDims;
};

const R_MIN = 4;
const R_MAX = 12;
const TICKS = [0, 0.25, 0.5, 0.75, 1];

/** Pure geometry: populated bins -> dot coordinates + the y=x reference. */
export function reliabilityModel(bins: ReliabilityBin[], dims: ChartDims): ReliabilityModel {
  const { x0, x1, y0, y1 } = plotArea(dims);
  const sx = linearScale([0, 1], [x0, x1]);
  const sy = linearScale([0, 1], [y1, y0]); // inverted so realized=1 is at the top
  const populated = bins.filter(
    (b): b is ReliabilityBin & { claimed: number; realized: number } =>
      b.claimed !== null && b.realized !== null && b.count > 0,
  );
  const maxCount = populated.reduce((mx, b) => Math.max(mx, b.count), 1);
  const points = populated.map((b) => ({
    cx: sx(b.claimed),
    cy: sy(b.realized),
    r: R_MIN + (b.count / maxCount) * (R_MAX - R_MIN),
    claimed: b.claimed,
    realized: b.realized,
    count: b.count,
  }));
  return { points, diagonal: { x1: sx(0), y1: sy(0), x2: sx(1), y2: sy(1) }, ticks: TICKS, dims };
}

const DIMS: ChartDims = { width: 360, height: 360, pad: 40 };

export default function ReliabilityChart({ bins }: { bins: ReliabilityBin[] }) {
  const m = reliabilityModel(bins, DIMS);
  const { x0, x1, y0, y1 } = plotArea(DIMS);
  const sx = linearScale([0, 1], [x0, x1]);
  const sy = linearScale([0, 1], [y1, y0]);

  return (
    <svg
      className={styles.chart}
      viewBox={`0 0 ${DIMS.width} ${DIMS.height}`}
      role="img"
      aria-label="Reliability curve: claimed probability versus realized frequency, with a y=x perfect-calibration reference."
      preserveAspectRatio="xMidYMid meet"
    >
      {m.ticks.map((t) => (
        <g key={t}>
          <line className={styles.grid} x1={sx(t)} y1={y0} x2={sx(t)} y2={y1} />
          <line className={styles.grid} x1={x0} y1={sy(t)} x2={x1} y2={sy(t)} />
          <text className={styles.tick} x={sx(t)} y={y1 + 16} textAnchor="middle">
            {t.toFixed(2)}
          </text>
          <text className={styles.tick} x={x0 - 8} y={sy(t) + 3} textAnchor="end">
            {t.toFixed(2)}
          </text>
        </g>
      ))}

      <line
        className={styles.diagonal}
        x1={m.diagonal.x1}
        y1={m.diagonal.y1}
        x2={m.diagonal.x2}
        y2={m.diagonal.y2}
      />

      {m.points.map((p, i) => (
        <circle key={i} className={styles.point} cx={p.cx} cy={p.cy} r={p.r}>
          <title>
            claimed {fmtPct(p.claimed)} · realized {fmtPct(p.realized)} · {p.count}{" "}
            {p.count === 1 ? "record" : "records"}
          </title>
        </circle>
      ))}

      <text className={styles.axisTitle} x={(x0 + x1) / 2} y={DIMS.height - 6} textAnchor="middle">
        Claimed probability
      </text>
      <text
        className={styles.axisTitle}
        x={-(y0 + y1) / 2}
        y={12}
        textAnchor="middle"
        transform="rotate(-90)"
      >
        Realized frequency
      </text>
    </svg>
  );
}
