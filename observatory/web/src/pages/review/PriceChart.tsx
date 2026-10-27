import {
  ColorType,
  createChart,
  LineStyle,
  PriceScaleMode,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Bar } from "../../api/client";

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

const time = (iso: string) => (Date.parse(`${iso}T00:00:00Z`) / 1000) as UTCTimestamp;

// One chart: the unadjusted closes (dashed) and the closes adjusted by the proposed factor
// (solid), with a marker on the ex-date. The price scale is logarithmic, so a hundredfold
// consolidation and a 1:1 bonus read the same way.
export function PriceChart({ bars, exDate }: { bars: Bar[]; exDate: string }) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!box.current) return;
    const chart = createChart(box.current, {
      height: 280,
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted") },
      grid: { vertLines: { visible: false }, horzLines: { color: css("--line") } },
      rightPriceScale: { borderVisible: false, mode: PriceScaleMode.Logarithmic },
      timeScale: { borderVisible: false },
      handleScroll: false,
      handleScale: false,
    });
    const raw = chart.addLineSeries({
      color: css("--muted"),
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: "unadjusted",
      priceLineVisible: false,
    });
    const adjusted = chart.addLineSeries({
      color: css("--accent"),
      lineWidth: 2,
      title: "adjusted",
      priceLineVisible: false,
    });
    raw.setData(bars.map((b) => ({ time: time(b.date), value: b.close })));
    adjusted.setData(bars.map((b) => ({ time: time(b.date), value: b.adjusted })));
    adjusted.setMarkers([
      { time: time(exDate), position: "aboveBar", shape: "arrowDown", color: css("--text"), text: "ex-date" },
    ]);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, exDate]);

  return <div className="chart" ref={box} role="img" aria-label="Closes around the ex-date" />;
}
