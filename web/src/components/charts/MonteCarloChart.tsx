"use client";

import { linearScale, niceTicks, plotArea, type ChartDims } from "@/lib/charts";
import { fmtPct, fmtUsd } from "@/lib/format";
import styles from "./MonteCarloChart.module.scss";

// The Monte-Carlo distribution of final bankroll — variance, not just the median. A
// box-and-whisker on a dollar axis: p5–p95 whisker, p25–p75 box, median line, mean dot. The
// initial bankroll is a reference; everything left of it is a losing outcome.

export type MonteCarlo = {
  n_sims: number;
  final_bankroll_p5: number;
  final_bankroll_p25: number;
  final_bankroll_median: number;
  final_bankroll_p75: number;
  final_bankroll_p95: number;
  final_bankroll_mean: number;
  median_max_drawdown: number;
  prob_loss: number;
};

export type McModel = {
  whisker: { x1: number; x2: number };
  box: { x1: number; x2: number };
  medianX: number;
  meanX: number;
  initialX: number;
  domain: [number, number];
  ticks: number[];
  probLoss: number;
  nSims: number;
  dims: ChartDims;
};

/** Pure geometry: percentiles -> box-and-whisker x-coordinates on a bankroll axis. */
export function mcModel(mc: MonteCarlo, initial: number, dims: ChartDims): McModel {
  const { x0, x1 } = plotArea(dims);
  const lo = Math.min(mc.final_bankroll_p5, initial);
  const hi = Math.max(mc.final_bankroll_p95, initial);
  const sx = linearScale([lo, hi], [x0, x1]);
  return {
    whisker: { x1: sx(mc.final_bankroll_p5), x2: sx(mc.final_bankroll_p95) },
    box: { x1: sx(mc.final_bankroll_p25), x2: sx(mc.final_bankroll_p75) },
    medianX: sx(mc.final_bankroll_median),
    meanX: sx(mc.final_bankroll_mean),
    initialX: sx(initial),
    domain: [lo, hi],
    ticks: niceTicks(lo, hi, 5),
    probLoss: mc.prob_loss,
    nSims: mc.n_sims,
    dims,
  };
}

const DIMS: ChartDims = { width: 520, height: 150, pad: 40 };

export default function MonteCarloChart({
  mc,
  initial,
}: {
  mc: MonteCarlo;
  initial: number;
}) {
  const m = mcModel(mc, initial, DIMS);
  const { x0, x1, y0, y1 } = plotArea(DIMS);
  const midY = (y0 + y1) / 2;
  const boxH = 26;
  const sx = linearScale(m.domain, [x0, x1]);

  return (
    <svg
      className={styles.chart}
      viewBox={`0 0 ${DIMS.width} ${DIMS.height}`}
      role="img"
      aria-label={`Monte-Carlo distribution of final bankroll over ${m.nSims} simulations; probability of loss ${fmtPct(m.probLoss)}.`}
      preserveAspectRatio="xMidYMid meet"
    >
      {/* loss zone: anything below the starting bankroll */}
      <rect className={styles.lossZone} x={x0} y={y0} width={Math.max(m.initialX - x0, 0)} height={y1 - y0} />

      {m.ticks.map((t) => (
        <g key={t}>
          <line className={styles.grid} x1={sx(t)} y1={y0} x2={sx(t)} y2={y1} />
          <text className={styles.tick} x={sx(t)} y={y1 + 16} textAnchor="middle">
            {fmtUsd(t)}
          </text>
        </g>
      ))}

      {/* p5–p95 whisker */}
      <line className={styles.whisker} x1={m.whisker.x1} y1={midY} x2={m.whisker.x2} y2={midY} />
      <line className={styles.cap} x1={m.whisker.x1} y1={midY - 7} x2={m.whisker.x1} y2={midY + 7} />
      <line className={styles.cap} x1={m.whisker.x2} y1={midY - 7} x2={m.whisker.x2} y2={midY + 7} />

      {/* p25–p75 box */}
      <rect
        className={styles.box}
        x={m.box.x1}
        y={midY - boxH / 2}
        width={Math.max(m.box.x2 - m.box.x1, 1)}
        height={boxH}
      />

      {/* median + mean */}
      <line className={styles.median} x1={m.medianX} y1={midY - boxH / 2} x2={m.medianX} y2={midY + boxH / 2} />
      <circle className={styles.mean} cx={m.meanX} cy={midY} r={3.5} />

      {/* initial bankroll reference */}
      <line className={styles.initial} x1={m.initialX} y1={y0} x2={m.initialX} y2={y1} />
      <text className={styles.initialLabel} x={m.initialX} y={y0 - 4} textAnchor="middle">
        start
      </text>
    </svg>
  );
}
