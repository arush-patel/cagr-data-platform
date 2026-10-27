import { useQuery } from "@tanstack/react-query";

import { getAdjustment, type Item } from "../../api/client";
import { formatNumber } from "../../format";
import { Decision } from "./Decision";
import { ItemHeader } from "./ItemHeader";
import { PriceChart } from "./PriceChart";

export function AdjustmentView({ id, onDecided }: { id: number; onDecided: (item: Item) => void }) {
  const { data: item, isError } = useQuery({
    queryKey: ["review", "adjustment", id],
    queryFn: () => getAdjustment(id),
  });
  if (isError) return <p className="error">Adjustment {id} couldn't be loaded.</p>;
  if (!item) return <p className="empty">Loading…</p>;

  const isinChanged = item.isin_before && item.isin_on && item.isin_before !== item.isin_on;
  return (
    <article>
      <ItemHeader item={item} dateLabel="ex-date" />
      <dl className="facts">
        <dt>Proposed factor</dt>
        <dd>{formatNumber(item.factor)}</dd>
        <dt>Expected from the action</dt>
        <dd>{formatNumber(item.expected_factor)}</dd>
        <dt>Observed in the closes</dt>
        <dd>{formatNumber(item.observed_factor)}</dd>
        <dt>Close before / on the ex-date</dt>
        <dd>
          {formatNumber(item.close_before, 6)} → {formatNumber(item.close_on, 6)}
        </dd>
        <dt>ISIN</dt>
        <dd>{isinChanged ? `${item.isin_before} → ${item.isin_on}` : (item.isin_on ?? "—")}</dd>
        {item.action && (
          <>
            <dt>NSE's announcement</dt>
            <dd>{item.action.subject}</dd>
          </>
        )}
      </dl>
      <h3 className="chart-title">
        Closes 20 sessions either side, unadjusted and adjusted by {formatNumber(item.chart_factor)}
      </h3>
      <p className="hint">A correct factor makes the adjusted line continuous across the ex-date.</p>
      {item.bars.length > 0 ? (
        <PriceChart bars={item.bars} exDate={item.date} />
      ) : (
        <p className="empty">No closes found around the ex-date.</p>
      )}
      <Decision
        key={item.id}
        item={item}
        approveLabel={`Approve factor ${formatNumber(item.chart_factor)}`}
        onDecided={onDecided}
      />
    </article>
  );
}
