import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { getItems, type Item, type ItemKind } from "../../api/client";
import { AdjustmentView } from "./AdjustmentView";
import { ChangeView } from "./ChangeView";
import { Queue } from "./Queue";

// The Review page (spec 0005, section 4): the queue on the left, the selected item on the right.
// The selection lives in the URL, so a link opens the same item.
export function ReviewPage() {
  const params = useParams();
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const tab = search.get("tab") === "history" ? "decided" : "pending";
  const { data: items, isError, isLoading } = useQuery({
    queryKey: ["review", tab],
    queryFn: () => getItems(tab),
  });

  const kind = params.kind as ItemKind | undefined;
  const id = params.id ? Number(params.id) : undefined;
  const open = (item: Item) => navigate({ pathname: `/review/${item.kind}/${item.id}`, search: search.toString() });

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!items?.length || event.target instanceof HTMLInputElement) return;
      if (event.target instanceof HTMLTextAreaElement) return;
      const at = items.findIndex((i) => i.kind === kind && i.id === id);
      if (event.key === "j") open(items[Math.min(at + 1, items.length - 1)]);
      if (event.key === "k" && at > 0) open(items[at - 1]);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="review">
      <aside className="queue">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "pending"} onClick={() => setSearch({})}>
            Waiting{tab === "pending" && items ? ` (${items.length})` : ""}
          </button>
          <button
            role="tab"
            aria-selected={tab === "decided"}
            onClick={() => setSearch({ tab: "history" })}
          >
            History
          </button>
        </div>
        {isLoading && <p className="empty">Loading…</p>}
        {isError && <p className="error">The queue couldn't be loaded. Is the API running?</p>}
        {items && <Queue items={items} history={tab === "decided"} selected={{ kind, id }} onOpen={open} />}
      </aside>
      <section className="item">
        {kind === "adjustment" && id !== undefined && <AdjustmentView id={id} />}
        {kind === "change" && id !== undefined && <ChangeView id={id} />}
        {(kind === undefined || id === undefined) && (
          <p className="empty">
            {items?.length === 0 && tab === "pending"
              ? "Nothing is waiting for review."
              : "Pick an item on the left to see why it is waiting. Keys j and k move through the list."}
          </p>
        )}
      </section>
    </div>
  );
}
