// Pure SVG geometry helpers shared by the hand-rolled charts. No React, no DOM — just maths,
// so each one is unit-tested in isolation (same discipline as `edgeMeterModel`). SVG y grows
// downward, so callers pass an inverted range (e.g. [height, 0]) to put larger values higher.

export type Point = { x: number; y: number };

/** A chart's pixel box. The plot area is inset by `pad` on every side (axis gutters). */
export type ChartDims = { width: number; height: number; pad: number };

/** Plot-area edges (in px) for a `dims`: x spans [x0,x1] left→right, y spans [y0(top),y1(bottom)]. */
export function plotArea(dims: ChartDims): { x0: number; x1: number; y0: number; y1: number } {
  return {
    x0: dims.pad,
    x1: dims.width - dims.pad,
    y0: dims.pad,
    y1: dims.height - dims.pad,
  };
}

/** Round to two decimals and drop trailing zeros, for compact, stable path strings. */
const r2 = (n: number): number => Number(n.toFixed(2));

/**
 * A linear map from a data `domain` to a pixel `range`. A zero-width domain (all values
 * equal) can't define a slope, so it centers on the range — a single point sits mid-axis
 * rather than dividing by zero.
 */
export function linearScale(
  domain: readonly [number, number],
  range: readonly [number, number],
): (value: number) => number {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  if (d0 === d1) {
    const mid = (r0 + r1) / 2;
    return () => mid;
  }
  const slope = (r1 - r0) / (d1 - d0);
  return (value: number) => r0 + (value - d0) * slope;
}

/** An SVG polyline `d`: move to the first point, line to the rest. Empty for no points. */
export function buildLinePath(points: readonly Point[]): string {
  if (points.length === 0) return "";
  return points.map((p, i) => `${i === 0 ? "M" : "L"} ${r2(p.x)} ${r2(p.y)}`).join(" ");
}

/** A closed area `d`: the line, then dropped to `baselineY` and back to the start. */
export function buildAreaPath(points: readonly Point[], baselineY: number): string {
  if (points.length === 0) return "";
  const last = points[points.length - 1]!;
  const first = points[0]!;
  return `${buildLinePath(points)} L ${r2(last.x)} ${r2(baselineY)} L ${r2(first.x)} ${r2(baselineY)} Z`;
}

/** A "nice" number ~`range`; rounds to 1/2/5/10 × 10^k (Heckbert's loose-label algorithm). */
function niceNum(range: number, round: boolean): number {
  const exp = Math.floor(Math.log10(range));
  const frac = range / 10 ** exp;
  let nice: number;
  if (round) nice = frac < 1.5 ? 1 : frac < 3 ? 2 : frac < 7 ? 5 : 10;
  else nice = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
  return nice * 10 ** exp;
}

/**
 * Round axis ticks bracketing `[min, max]`, ~`count` of them, snapped to nice round steps.
 * Returns a single tick for a degenerate (single-value) domain. Ticks are rounded to the
 * step's own precision so the labels stay clean (no 0.30000000000000004).
 */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (count < 2 || min === max) return [min];
  const step = niceNum(niceNum(max - min, false) / (count - 1), true);
  const niceMin = Math.floor(min / step) * step;
  const niceMax = Math.ceil(max / step) * step;
  const decimals = Math.max(0, -Math.floor(Math.log10(step)));
  const ticks: number[] = [];
  for (let v = niceMin; v <= niceMax + step / 2; v += step) {
    ticks.push(Number(v.toFixed(decimals)));
  }
  return ticks;
}
