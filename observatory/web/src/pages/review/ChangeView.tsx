import { useQuery } from "@tanstack/react-query";

import { getChange, type Item } from "../../api/client";
import { LineChart } from "../../components/LineChart";
import { formatDate } from "../../format";
import { Decision } from "./Decision";
import { Explainer } from "./Explainer";
import { ItemHeader } from "./ItemHeader";

// The listing the change is on, then each candidate.
const COLORS = ["var(--accent)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)"];

const EVIDENCE_LABELS: Record<string, string> = {
  rule: "Rule",
  session: "Session",
  file_ids: "Files read",
  row: "Bhavcopy row",
  previous: "Previous row",
  symbolchange: "symbolchange.csv row",
  candidates: "Could belong to",
};

const PAYLOAD_LABELS: Record<string, string> = {
  old_isin: "Old ISIN",
  new_isin: "New ISIN",
  old_symbol: "Old symbol",
  new_symbol: "New symbol",
  old_name: "Old name",
  new_name: "New name",
};
// The listings table already shows these.
const HIDDEN_PAYLOAD = new Set(["merged_id", "into_id"]);

function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.map(show).join(", ");
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k} ${show(v)}`)
      .join(" · ");
  }
  return String(value);
}

export function ChangeView({ id, onDecided }: { id: number; onDecided: (item: Item) => void }) {
  const { data: item, isError } = useQuery({
    queryKey: ["review", "change", id],
    queryFn: () => getChange(id),
  });
  if (isError) return <p className="error">Change {id} couldn't be loaded.</p>;
  if (!item) return <p className="empty">Loading…</p>;

  const payload = Object.entries(item.payload).filter(([key]) => !HIDDEN_PAYLOAD.has(key));
  const symbolOf = new Map(item.listings.map((l) => [l.listing_id, l.symbol]));
  const describe = (key: string, value: unknown) =>
    key === "candidates" && Array.isArray(value)
      ? value.map((id) => `${symbolOf.get(Number(id)) ?? "?"} (listing ${id})`).join(", ")
      : show(value);
  const others = item.listings.slice(1);
  const isRename = item.change_kind === "index_rename";
  const approveLabel =
    item.change_kind === "merge"
      ? `Approve: merge into ${others.map((l) => l.symbol).join(", ") || "the survivor"}`
      : isRename
        ? "Approve: it is a rename"
        : "Approve";
  const lines = item.series
    .filter((s) => s.closes.length > 0)
    .map((s) => ({
      label: `${s.symbol ?? `Listing ${s.listing_id}`}${s.listing_id === item.listing_id ? " (new)" : ""}`,
      color: COLORS[item.series.indexOf(s) % COLORS.length],
      points: s.closes.map((c) => ({ date: c.date, value: c.close })),
    }));
  return (
    <article>
      <ItemHeader item={item} dateLabel="from" />
      <table className="listings">
        <caption>The listings involved</caption>
        <thead>
          <tr>
            <th>Listing</th>
            <th>Symbol</th>
            <th>ISIN</th>
            <th>Name</th>
            <th>First session</th>
            <th>Last session</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {item.listings.map((l) => (
            <tr key={l.listing_id}>
              <td>{l.listing_id}</td>
              <td>{l.symbol ?? "—"}</td>
              <td>{l.isin ?? "—"}</td>
              <td>{l.name ?? "—"}</td>
              <td>{formatDate(l.first_session)}</td>
              <td>{formatDate(l.last_session)}</td>
              <td>{l.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="facts">
        {payload.map(([key, value]) => (
          <div key={key} className="fact">
            <dt>{PAYLOAD_LABELS[key] ?? key}</dt>
            <dd>{show(value)}</dd>
          </div>
        ))}
        {Object.entries(item.evidence).map(([key, value]) => (
          <div key={key} className="fact">
            <dt>{EVIDENCE_LABELS[key] ?? key}</dt>
            <dd>{describe(key, value)}</dd>
          </div>
        ))}
      </dl>
      <Explainer guide={item.guide} />
      <h3 className="chart-title">{isRename ? "The new index and the ones that stopped" : "Both listings' closes"}</h3>
      <p className="hint">
        The last 40 sessions each listing traded before {formatDate(item.date)} and the first 40 from it, as traded.
      </p>
      {lines.length > 0 ? (
        <LineChart lines={lines} mark={{ date: item.date, label: formatDate(item.date) }} />
      ) : (
        <p className="empty">None of these listings has closes around {formatDate(item.date)}.</p>
      )}
      <Decision
        key={item.id}
        item={item}
        approveLabel={approveLabel}
        approveEffect={item.approve_effect}
        rejectEffect={item.reject_effect}
        choices={isRename ? others : undefined}
        onDecided={onDecided}
      />
    </article>
  );
}
