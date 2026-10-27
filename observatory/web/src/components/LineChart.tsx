import { useMemo, useRef, useState } from "react";

import { formatDate, formatNumber } from "../format";

export type Line = {
  label: string;
  /** A CSS colour, usually a variable such as var(--accent). */
  color: string;
  dashed?: boolean;
  points: { date: string; value: number }[];
};

type Props = {
  lines: Line[];
  /** The day the item is about: a dashed rule just before its bar, labelled. */
  mark: { date: string; label: string };
  /** A note drawn beside the rule, such as how long trading had stopped. */
  gapNote?: string;
  height?: number;
};

const PAD = { top: 28, right: 64, bottom: 28, left: 12 };
const WIDTH = 860;

// 1, 2 and 5 in each decade: on a log scale these read as round prices.
function logTicks(lo: number, hi: number): number[] {
  const ticks: number[] = [];
  for (let e = Math.floor(Math.log10(lo)) - 1; e <= Math.ceil(Math.log10(hi)); e++) {
    for (const m of [1, 2, 5]) {
      const t = m * 10 ** e;
      if (t >= lo && t <= hi) ticks.push(t);
    }
  }
  if (ticks.length > 8) return ticks.filter((t) => Number(t.toPrecision(1)).toString().startsWith("1"));
  if (ticks.length >= 3) return ticks;
  // A narrow range: evenly spaced round values instead.
  const step = 10 ** Math.floor(Math.log10((hi - lo) / 4 || hi));
  const nice = [1, 2, 2.5, 5, 10].map((m) => m * step).find((s) => (hi - lo) / s <= 6) ?? step * 10;
  const out: number[] = [];
  for (let t = Math.ceil(lo / nice) * nice; t <= hi; t += nice) out.push(Number(t.toPrecision(6)));
  return out;
}

// Closes on a log scale, so a tenfold and a halving read as moves of the same kind. The x axis
// counts sessions with a bar, not calendar days, so a long halt doesn't squash the chart; the
// gap note says how long it was.
export function LineChart({ lines, mark, gapNote, height = 300 }: Props) {
  const box = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const chart = useMemo(() => {
    const dates = [...new Set(lines.flatMap((l) => l.points.map((p) => p.date)))].sort();
    const at = new Map(dates.map((d, i) => [d, i]));
    const values = lines.flatMap((l) => l.points.map((p) => p.value)).filter((v) => v > 0);
    if (dates.length === 0 || values.length === 0) return null;
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    if (hi / lo < 1.1) [lo, hi] = [lo / 1.05, hi * 1.05];
    const pad = (Math.log(hi) - Math.log(lo)) * 0.06;
    const [llo, lhi] = [Math.log(lo) - pad, Math.log(hi) + pad];
    const plotW = WIDTH - PAD.left - PAD.right;
    const plotH = height - PAD.top - PAD.bottom;
    const x = (i: number) => PAD.left + (dates.length === 1 ? plotW / 2 : (i / (dates.length - 1)) * plotW);
    const y = (v: number) => PAD.top + (1 - (Math.log(v) - llo) / (lhi - llo)) * plotH;
    const markAt = dates.findIndex((d) => d >= mark.date);
    const markX = markAt <= 0 ? x(Math.max(markAt, 0)) : (x(markAt - 1) + x(markAt)) / 2;
    const paths = lines.map((l) =>
      l.points
        .filter((p) => p.value > 0)
        .map((p, n) => `${n ? "L" : "M"}${x(at.get(p.date)!).toFixed(1)},${y(p.value).toFixed(1)}`)
        .join(""),
    );
    const xTicks = [...new Set([0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * (dates.length - 1))))];
    return { dates, at, x, y, markX, paths, ticks: logTicks(lo, hi), xTicks, plotW };
  }, [lines, mark.date, height]);

  if (!chart) return <p className="empty">No closes to draw.</p>;
  const { dates, at, x, y, markX, paths, ticks, xTicks } = chart;

  function onMove(event: React.MouseEvent<SVGSVGElement>) {
    const rect = box.current?.getBoundingClientRect();
    if (!rect) return;
    const px = ((event.clientX - rect.left) / rect.width) * WIDTH;
    const f = (px - PAD.left) / (WIDTH - PAD.left - PAD.right);
    setHover(Math.min(dates.length - 1, Math.max(0, Math.round(f * (dates.length - 1)))));
  }

  const hovered = hover === null ? null : dates[hover];
  return (
    <figure className="line-chart">
      <figcaption className="legend">
        {lines.map((l) => {
          const p = hovered ? l.points.find((q) => q.date === hovered) : undefined;
          return (
            <span key={l.label}>
              <svg width="22" height="8" aria-hidden="true">
                <line x1="0" y1="4" x2="22" y2="4" stroke={l.color} strokeWidth="2" strokeDasharray={l.dashed ? "4 3" : undefined} />
              </svg>
              {l.label}
              {hovered && <strong> {p ? formatNumber(p.value, 6) : "—"}</strong>}
            </span>
          );
        })}
        {hovered && <span className="legend-date">{formatDate(hovered)}</span>}
      </figcaption>
      <svg
        ref={box}
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={`Closes around ${formatDate(mark.date)}`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={PAD.left} x2={WIDTH - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="axis" x={WIDTH - PAD.right + 6} y={y(t) + 4}>
              {formatNumber(t, 4)}
            </text>
          </g>
        ))}
        {xTicks.map((i) => (
          <text
            key={i}
            className="axis"
            x={x(i)}
            y={height - 8}
            textAnchor={i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle"}
          >
            {formatDate(dates[i])}
          </text>
        ))}
        <line className="mark" x1={markX} x2={markX} y1={PAD.top - 8} y2={height - PAD.bottom} />
        <text className="mark-label" x={markX} y={PAD.top - 12} textAnchor="middle">
          {mark.label}
        </text>
        {gapNote && (
          <text className="gap-note" x={markX + 6} y={height - PAD.bottom - 8}>
            {gapNote}
          </text>
        )}
        {lines.map((l, n) => (
          <path
            key={l.label}
            d={paths[n]}
            fill="none"
            stroke={l.color}
            strokeWidth={l.dashed ? 1.5 : 2}
            strokeDasharray={l.dashed ? "5 4" : undefined}
            strokeLinejoin="round"
          />
        ))}
        {hover !== null && (
          <>
            <line className="cursor" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={height - PAD.bottom} />
            {lines.map((l) => {
              const p = l.points.find((q) => q.date === dates[hover]);
              return p && p.value > 0 && at.has(p.date) ? (
                <circle key={l.label} cx={x(hover)} cy={y(p.value)} r="3.5" fill={l.color} />
              ) : null;
            })}
          </>
        )}
      </svg>
    </figure>
  );
}
