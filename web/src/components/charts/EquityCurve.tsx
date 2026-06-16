"use client";

import {
  buildAreaPath,
  buildLinePath,
  linearScale,
  niceTicks,
  plotArea,
  type ChartDims,
  type Point,
} from "@/lib/charts";
import { fmtPct, fmtUsd } from "@/lib/format";
import styles from "./EquityCurve.module.scss";

// Realized equity over the replay, starting from the initial bankroll, with the worst
// peak->trough drawdown shaded. x is the resolution step (the equity_curve is sampled at
// each resolution); a dashed reference marks the starting bankroll.

export type EquityCurvePoint = { time: string; equity: number };

export type DrawdownSpan = {
  peakIndex: number;
  troughIndex: number;
  frac: number;
  peak: Point;
  trough: Point;
} | null;

export type EquityModel = {
  points: Point[];
  equities: number[];
  linePath: string;
  areaPath: string;
  yTicks: number[];
  yMin: number;
  yMax: number;
  initialY: number;
  drawdown: DrawdownSpan;
  dims: ChartDims;
};

/** Pure geometry: equity samples (initial bankroll prepended) -> path + drawdown span. */
export function equityModel(
  curve: EquityCurvePoint[],
  initial: number,
  dims: ChartDims,
): EquityModel {
  const { x0, x1, y0, y1 } = plotArea(dims);
  const equities = [initial, ...curve.map((c) => c.equity)];
  const yMin = Math.min(...equities);
  const yMax = Math.max(...equities);
  const sx = linearScale([0, Math.max(equities.length - 1, 1)], [x0, x1]);
  const sy = linearScale([yMin, yMax], [y1, y0]);
  const points = equities.map((e, i) => ({ x: sx(i), y: sy(e) }));

  // Worst peak->trough decline: track the running peak and the largest drop beneath it.
  let peak = equities[0]!;
  let peakIdx = 0;
  let worst = 0;
  let ddPeakIdx = 0;
  let ddTroughIdx = 0;
  equities.forEach((e, i) => {
    if (e > peak) {
      peak = e;
      peakIdx = i;
    }
    const dd = peak > 0 ? (peak - e) / peak : 0;
    if (dd > worst) {
      worst = dd;
      ddPeakIdx = peakIdx;
      ddTroughIdx = i;
    }
  });
  const drawdown: DrawdownSpan =
    worst > 0
      ? {
          peakIndex: ddPeakIdx,
          troughIndex: ddTroughIdx,
          frac: worst,
          peak: points[ddPeakIdx]!,
          trough: points[ddTroughIdx]!,
        }
      : null;

  return {
    points,
    equities,
    linePath: buildLinePath(points),
    areaPath: buildAreaPath(points, y1),
    yTicks: niceTicks(yMin, yMax, 4),
    yMin,
    yMax,
    initialY: sy(initial),
    drawdown,
    dims,
  };
}

const DIMS: ChartDims = { width: 520, height: 260, pad: 44 };

export default function EquityCurve({
  curve,
  initial,
}: {
  curve: EquityCurvePoint[];
  initial: number;
}) {
  const m = equityModel(curve, initial, DIMS);
  const { x0, x1, y0, y1 } = plotArea(DIMS);
  const sy = linearScale([m.yMin, m.yMax], [y1, y0]);

  return (
    <svg
      className={styles.chart}
      viewBox={`0 0 ${DIMS.width} ${DIMS.height}`}
      role="img"
      aria-label="Equity curve over the backtest replay, with the worst peak-to-trough drawdown shaded."
      preserveAspectRatio="xMidYMid meet"
    >
      {m.yTicks.map((t) => (
        <g key={t}>
          <line className={styles.grid} x1={x0} y1={sy(t)} x2={x1} y2={sy(t)} />
          <text className={styles.tick} x={x0 - 8} y={sy(t) + 3} textAnchor="end">
            {fmtUsd(t)}
          </text>
        </g>
      ))}

      {m.drawdown && (
        <rect
          className={styles.ddBand}
          x={m.drawdown.peak.x}
          y={y0}
          width={Math.max(m.drawdown.trough.x - m.drawdown.peak.x, 0)}
          height={y1 - y0}
        >
          <title>max drawdown {fmtPct(m.drawdown.frac)}</title>
        </rect>
      )}

      <path className={styles.area} d={m.areaPath} />
      <path className={styles.line} d={m.linePath} />

      <line
        className={styles.initial}
        x1={x0}
        y1={m.initialY}
        x2={x1}
        y2={m.initialY}
      />
      <text className={styles.initialLabel} x={x1} y={m.initialY - 5} textAnchor="end">
        start {fmtUsd(initial)}
      </text>

      {m.drawdown && (
        <circle className={styles.trough} cx={m.drawdown.trough.x} cy={m.drawdown.trough.y} r={3.5}>
          <title>trough · max drawdown {fmtPct(m.drawdown.frac)}</title>
        </circle>
      )}
    </svg>
  );
}
