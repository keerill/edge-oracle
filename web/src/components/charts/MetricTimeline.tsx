"use client";

import {
  buildLinePath,
  linearScale,
  niceTicks,
  plotArea,
  type ChartDims,
  type Point,
} from "@/lib/charts";
import styles from "./MetricTimeline.module.scss";

// Cumulative Brier & log-loss as the journal grows — both "lower is better" losses, plotted on
// a shared axis so you can watch them settle (or drift) as evidence accrues. x is the journal
// step (one point per distinct resolution time).

export type TimelinePoint = { time: string; n: number; brier: number; log_loss: number };

export type TimelineModel = {
  brierPoints: Point[];
  logLossPoints: Point[];
  brierPath: string;
  logLossPath: string;
  yMax: number;
  yTicks: number[];
  xLabels: { first: string; last: string };
  dims: ChartDims;
};

const EMPTY = (dims: ChartDims): TimelineModel => ({
  brierPoints: [],
  logLossPoints: [],
  brierPath: "",
  logLossPath: "",
  yMax: 0,
  yTicks: [],
  xLabels: { first: "", last: "" },
  dims,
});

/** Pure geometry: cumulative points -> two polylines on a shared [0, yMax] axis. */
export function timelineModel(points: TimelinePoint[], dims: ChartDims): TimelineModel {
  if (points.length === 0) return EMPTY(dims);
  const { x0, x1, y0, y1 } = plotArea(dims);
  const yMax = Math.max(...points.flatMap((p) => [p.brier, p.log_loss]));
  const sx = linearScale([0, Math.max(points.length - 1, 1)], [x0, x1]);
  const sy = linearScale([0, yMax === 0 ? 1 : yMax], [y1, y0]);
  const brierPoints = points.map((p, i) => ({ x: sx(i), y: sy(p.brier) }));
  const logLossPoints = points.map((p, i) => ({ x: sx(i), y: sy(p.log_loss) }));
  return {
    brierPoints,
    logLossPoints,
    brierPath: buildLinePath(brierPoints),
    logLossPath: buildLinePath(logLossPoints),
    yMax,
    yTicks: niceTicks(0, yMax === 0 ? 1 : yMax, 4),
    xLabels: { first: points[0]!.time.slice(0, 10), last: points[points.length - 1]!.time.slice(0, 10) },
    dims,
  };
}

const DIMS: ChartDims = { width: 520, height: 260, pad: 38 };

export default function MetricTimeline({ points }: { points: TimelinePoint[] }) {
  const m = timelineModel(points, DIMS);
  const { x0, x1, y0, y1 } = plotArea(DIMS);
  const sy = linearScale([0, m.yMax === 0 ? 1 : m.yMax], [y1, y0]);

  return (
    <div className={styles.wrap}>
      <div className={styles.legend} aria-hidden="true">
        <span className={styles.key}>
          <i className={styles.swatchBrier} /> Brier
        </span>
        <span className={styles.key}>
          <i className={styles.swatchLog} /> Log-loss (nats)
        </span>
      </div>
      <svg
        className={styles.chart}
        viewBox={`0 0 ${DIMS.width} ${DIMS.height}`}
        role="img"
        aria-label="Cumulative Brier score and log-loss over the calibration journal; lower is better."
        preserveAspectRatio="xMidYMid meet"
      >
        {m.yTicks.map((t) => (
          <g key={t}>
            <line className={styles.grid} x1={x0} y1={sy(t)} x2={x1} y2={sy(t)} />
            <text className={styles.tick} x={x0 - 8} y={sy(t) + 3} textAnchor="end">
              {t}
            </text>
          </g>
        ))}

        <path className={styles.brierLine} d={m.brierPath} />
        <path className={styles.logLine} d={m.logLossPath} />

        {m.brierPoints.map((p, i) => (
          <circle key={`b${i}`} className={styles.brierDot} cx={p.x} cy={p.y} r={2.5}>
            <title>
              step {i + 1} · Brier {points[i]!.brier.toFixed(4)} · n {points[i]!.n}
            </title>
          </circle>
        ))}
        {m.logLossPoints.map((p, i) => (
          <circle key={`l${i}`} className={styles.logDot} cx={p.x} cy={p.y} r={2.5}>
            <title>
              step {i + 1} · log-loss {points[i]!.log_loss.toFixed(4)} · n {points[i]!.n}
            </title>
          </circle>
        ))}

        {m.xLabels.first && (
          <text className={styles.tick} x={x0} y={y1 + 18} textAnchor="start">
            {m.xLabels.first}
          </text>
        )}
        {m.xLabels.last && (
          <text className={styles.tick} x={x1} y={y1 + 18} textAnchor="end">
            {m.xLabels.last}
          </text>
        )}
      </svg>
    </div>
  );
}
