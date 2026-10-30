import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { getItems, type Item, type ItemKind } from "../../api/client";
import { AdjustmentView } from "./AdjustmentView";
import { ChangeView } from "./ChangeView";
import { Queue } from "./Queue";

export const SORTS = {
  newest: { label: "Newest date first", compare: (a: Item, b: Item) => b.date.localeCompare(a.date) || b.id - a.id },
  oldest: { label: "Oldest date first", compare: (a: Item, b: Item) => a.date.localeCompare(b.date) || a.id - b.id },
  waiting: {
    label: "Waiting longest",
    compare: (a: Item, b: Item) => b.waiting_sessions - a.waiting_sessions || a.date.localeCompare(b.date),
  },
  decided: {
    label: "Decided most recently",
    compare: (a: Item, b: Item) => (b.decided_at ?? "").localeCompare(a.decided_at ?? ""),
  },
  symbol: {
    label: "Symbol A–Z",
    compare: (a: Item, b: Item) => (a.symbol ?? "").localeCompare(b.symbol ?? "") || a.date.localeCompare(b.date),
  },
} as const;
export type Sort = keyof typeof SORTS;

// The Review page (spec 0005, section 4): the queue on the left, the selected item on the right.
// The selection, the tab, the type filter, the search and the sort live in the URL, so a link
// opens the same view.
export function ReviewPage() {
  const params = useParams();
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const tab = search.get("tab") === "history" ? "decided" : "pending";
  const type = search.get("type") ?? "";
  const text = search.get("q") ?? "";
  const sortParam = search.get("sort") as Sort | null;
  const sort: Sort = sortParam && sortParam in SORTS ? sortParam : tab === "decided" ? "decided" : "newest";
  const { data, error, isLoading } = useQuery({
    queryKey: ["review", tab],
    queryFn: () => getItems(tab),
  });

  // Every type in this tab, with how many items it has, in the API's order.
  const types = useMemo(() => {
    const counts = new Map<string, { label: string; count: number }>();
    for (const i of data ?? []) {
      const c = counts.get(i.category) ?? { label: i.category_label, count: 0 };
      counts.set(i.category, { ...c, count: c.count + 1 });
    }
    return [...counts.entries()].map(([category, c]) => ({ category, ...c }));
  }, [data]);

  const items = useMemo(() => {
    if (!data) return undefined;
    const needle = text.trim().toLowerCase();
    return data
      .filter((i) => !type || i.category === type)
      .filter(
        (i) =>
          !needle ||
          (i.symbol ?? "").toLowerCase().includes(needle) ||
          (i.name ?? "").toLowerCase().includes(needle),
      )
      .sort(SORTS[sort].compare);
  }, [data, type, text, sort]);

  const kind = params.kind as ItemKind | undefined;
  const id = params.id ? Number(params.id) : undefined;
  const open = (item: Item) => navigate({ pathname: `/review/${item.kind}/${item.id}`, search: search.toString() });
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(search);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearch(next, { replace: true });
  };

  // The last decision stays in view on the next item, so a wrong button is noticed at once.
  const [lastDecided, setLastDecided] = useState<Item | null>(null);

  // After a decision, open the next item in the list, or the empty page after the last one.
  const next = (decided: Item) => {
    setLastDecided(decided);
    const list = items ?? [];
    const at = list.findIndex((i) => i.kind === decided.kind && i.id === decided.id);
    const following = list[at + 1] ?? (at > 0 ? list[at - 1] : undefined);
    if (following) open(following);
    else navigate({ pathname: "/review", search: search.toString() });
  };

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!items?.length) return;
      const target = event.target;
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
      if (target instanceof HTMLSelectElement) return;
      const at = items.findIndex((i) => i.kind === kind && i.id === id);
      if (event.key === "j") open(items[Math.min(at + 1, items.length - 1)]);
      if (event.key === "k" && at > 0) open(items[at - 1]);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const total = data?.length ?? 0;
  return (
    <div className="review">
      <aside className="queue">
        <div className="queue-controls">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "pending"} onClick={() => setSearch({})}>
              Waiting{tab === "pending" && data ? ` (${total})` : ""}
            </button>
            <button role="tab" aria-selected={tab === "decided"} onClick={() => setSearch({ tab: "history" })}>
              History
            </button>
          </div>
          <label className="control">
            <span>Type</span>
            <select value={type} onChange={(e) => update("type", e.target.value)}>
              <option value="">All types ({total})</option>
              {types.map((t) => (
                <option key={t.category} value={t.category}>
                  {t.label} ({t.count})
                </option>
              ))}
            </select>
          </label>
          <div className="control-row">
            <label className="control">
              <span>Sort</span>
              <select value={sort} onChange={(e) => update("sort", e.target.value)}>
                {(Object.keys(SORTS) as Sort[])
                  .filter((s) => (tab === "decided" ? s !== "waiting" : s !== "decided"))
                  .map((s) => (
                    <option key={s} value={s}>
                      {SORTS[s].label}
                    </option>
                  ))}
              </select>
            </label>
            <label className="control">
              <span>Symbol or name</span>
              <input type="search" value={text} onChange={(e) => update("q", e.target.value)} />
            </label>
          </div>
          {items && items.length !== total && (
            <p className="hint">
              Showing {items.length} of {total}
            </p>
          )}
        </div>
        <div className="queue-list">
          {isLoading && <p className="empty">Loading…</p>}
          {error && <p className="error">The queue couldn't be loaded: {error.message}</p>}
          {items && <Queue items={items} history={tab === "decided"} selected={{ kind, id }} onOpen={open} />}
        </div>
      </aside>
      <section className="item">
        {lastDecided && (
          <p className={`last-decided ${lastDecided.status}`} role="status">
            {lastDecided.status === "applied" ? "Approved" : "Rejected"}{" "}
            <strong>{lastDecided.symbol ?? `listing ${lastDecided.listing_id}`}</strong> · {lastDecided.title}
            {lastDecided.note && <> · “{lastDecided.note}”</>}
          </p>
        )}
        {kind === "adjustment" && id !== undefined && <AdjustmentView id={id} onDecided={next} />}
        {kind === "change" && id !== undefined && <ChangeView id={id} onDecided={next} />}
        {(kind === undefined || id === undefined) && (
          <p className="empty">
            {total === 0 && tab === "pending" && data
              ? "Nothing is waiting for review."
              : "Pick an item on the left to see why it is waiting. Keys j and k move through the list."}
          </p>
        )}
      </section>
    </div>
  );
}
