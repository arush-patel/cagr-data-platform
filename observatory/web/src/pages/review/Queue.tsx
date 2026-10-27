import type { Item, ItemKind } from "../../api/client";
import { formatDate, sessions } from "../../format";

const GROUPS: { kind: ItemKind; label: string }[] = [
  { kind: "adjustment", label: "Price adjustments" },
  { kind: "change", label: "Listing changes" },
];

type Props = {
  items: Item[];
  history: boolean;
  selected: { kind?: string; id?: number };
  onOpen: (item: Item) => void;
};

export function Queue({ items, history, selected, onOpen }: Props) {
  if (items.length === 0) {
    return <p className="empty">{history ? "Nothing has been decided yet." : "Nothing is waiting for review."}</p>;
  }
  return (
    <>
      {GROUPS.map(({ kind, label }) => {
        const group = items.filter((i) => i.kind === kind);
        if (group.length === 0) return null;
        return (
          <div key={kind} className="group">
            <h3>
              {label} ({group.length})
            </h3>
            <ul>
              {group.map((item) => (
                <li key={item.id}>
                  <button
                    className={selected.kind === item.kind && selected.id === item.id ? "row selected" : "row"}
                    onClick={() => onOpen(item)}
                  >
                    <span className="row-main">
                      <strong>{item.symbol ?? `#${item.listing_id}`}</strong> {item.title}
                    </span>
                    <span className="row-side">
                      {history ? (
                        <span className={`status ${item.status}`}>{item.status}</span>
                      ) : (
                        <span className={item.overdue ? "age overdue" : "age"} title="Sessions waiting">
                          {sessions(item.waiting_sessions)}
                        </span>
                      )}
                    </span>
                    <span className="row-sub">{formatDate(item.date)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </>
  );
}
