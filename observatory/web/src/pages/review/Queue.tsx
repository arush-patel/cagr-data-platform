import { useEffect, useRef, useState } from "react";

import type { Item } from "../../api/client";
import { formatDate, sessions } from "../../format";

// Rows are drawn a page at a time, so a queue of thousands stays quick.
const PAGE = 200;

type Props = {
  items: Item[];
  history: boolean;
  selected: { kind?: string; id?: number };
  onOpen: (item: Item) => void;
};

export function Queue({ items, history, selected, onOpen }: Props) {
  const [shown, setShown] = useState(PAGE);
  const selectedRow = useRef<HTMLButtonElement>(null);
  const at = items.findIndex((i) => i.kind === selected.kind && i.id === selected.id);

  // Keep the selected row drawn and in view as j and k move through the list.
  useEffect(() => {
    if (at >= shown) setShown(Math.ceil((at + 1) / PAGE) * PAGE);
  }, [at, shown]);
  useEffect(() => {
    selectedRow.current?.scrollIntoView?.({ block: "nearest" });
  }, [at]);

  if (items.length === 0) {
    return <p className="empty">{history ? "Nothing has been decided yet." : "Nothing matches."}</p>;
  }
  return (
    <>
      <ul className="rows">
        {items.slice(0, shown).map((item) => {
          const isSelected = selected.kind === item.kind && selected.id === item.id;
          return (
            <li key={`${item.kind}-${item.id}`}>
              <button
                ref={isSelected ? selectedRow : undefined}
                className={isSelected ? "row selected" : "row"}
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
                <span className="row-sub">
                  {formatDate(item.date)} · {item.category_label}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {items.length > shown && (
        <button className="more" onClick={() => setShown(shown + PAGE)}>
          Show {Math.min(PAGE, items.length - shown)} more of {items.length - shown}
        </button>
      )}
    </>
  );
}
