import { useQuery } from "@tanstack/react-query";

import { getAdjustment, type Item } from "../../api/client";
import { LineChart } from "../../components/LineChart";
import { formatDate, formatNumber, sessions } from "../../format";
import { Decision } from "./Decision";
import { Explainer } from "./Explainer";
import { ItemHeader } from "./ItemHeader";

const X = "×";

export function AdjustmentView({ id, onDecided }: { id: number; onDecided: (item: Item) => void }) {
  const { data: item, isError } = useQuery({
    queryKey: ["review", "adjustment", id],
    queryFn: () => getAdjustment(id),
  });
  if (isError) return <p className="error">Adjustment {id} couldn't be loaded.</p>;
  if (!item) return <p className="empty">Loading…</p>;

  const isinChanged = item.isin_before && item.isin_on && item.isin_before !== item.isin_on;
  const factor = item.chart_factor;
  const before = item.bars.filter((b) => b.date < item.date);
  const lines = [
    {
      label: "As traded",
      color: "var(--muted)",
      dashed: true,
      points: before.map((b) => ({ date: b.date, value: b.close })),
    },
    {
      label: factor ? `Adjusted by ${X}${formatNumber(factor)}` : "As traded from the ex-date",
      color: "var(--accent)",
      points: item.bars.map((b) => ({ date: b.date, value: b.date < item.date && factor ? b.close * factor : b.close })),
    },
  ];
  const silent = item.silent_sessions ?? 0;
  return (
    <article>
      <ItemHeader item={item} dateLabel="ex-date" />
      <dl className="facts">
        <dt>Move on the ex-date</dt>
        <dd>
          {X}
          {formatNumber(item.observed_factor)} · close {formatNumber(item.close_before, 6)} →{" "}
          {formatNumber(item.close_on, 6)}
        </dd>
        <dt>Previous trade</dt>
        <dd className={silent > 0 ? "flag" : undefined}>
          {item.previous_trade ? formatDate(item.previous_trade) : "none"}
          {silent > 0 && ` · ${sessions(silent)} without a trade before the ex-date`}
        </dd>
        <dt>ISIN</dt>
        <dd className={isinChanged ? "flag" : undefined}>
          {isinChanged ? `${item.isin_before} → ${item.isin_on} (changed)` : (item.isin_on ?? "—")}
        </dd>
        {item.action ? (
          <>
            <dt>NSE's announcement</dt>
            <dd>
              “{item.action.subject}” <span className="hint">({item.action.kind})</span>
            </dd>
            <dt>Factor the announcement predicts</dt>
            <dd>{formatNumber(item.expected_factor)}</dd>
          </>
        ) : (
          <>
            <dt>NSE's announcement</dt>
            <dd>none for this date</dd>
          </>
        )}
        <dt>Factor approving applies</dt>
        <dd>
          <strong>{factor ? `${X}${formatNumber(factor)}` : "—"}</strong>
          {factor && !item.factor && (
            <span className="hint"> ({item.expected_factor ? "the announced one" : "the observed move"})</span>
          )}
        </dd>
      </dl>
      <Explainer guide={item.guide} />
      <h3 className="chart-title">The closes either side of the ex-date</h3>
      <p className="hint">
        The dashed line is the price as it traded. The solid line is the history after approving: if the factor is
        right, it runs on without a step at the ex-date.
      </p>
      {item.bars.length > 0 ? (
        <LineChart
          lines={lines}
          mark={{ date: item.date, label: `ex-date ${formatDate(item.date)}` }}
          gapNote={silent > 0 ? `${sessions(silent)} without a trade` : undefined}
        />
      ) : (
        <p className="empty">No closes found around the ex-date.</p>
      )}
      <Decision
        key={item.id}
        item={item}
        approveLabel={factor ? `Approve ${X}${formatNumber(factor)}` : "Approve"}
        approveEffect={item.approve_effect}
        rejectEffect={item.reject_effect}
        onDecided={onDecided}
      />
    </article>
  );
}
