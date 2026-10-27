import type { Item } from "../../api/client";
import { formatDate, sessions } from "../../format";

// The words first: what the item is, and the one sentence that says why it is waiting.
export function ItemHeader({ item, dateLabel }: { item: Item; dateLabel: string }) {
  return (
    <header className="item-header">
      <h2>
        {item.symbol ?? `Listing ${item.listing_id}`} · {item.title}
      </h2>
      <p className="meta">
        {item.name && <>{item.name} · </>}
        {dateLabel} {formatDate(item.date)} · listing {item.listing_id}
        {item.status === "pending" ? (
          <> · waiting {sessions(item.waiting_sessions)}</>
        ) : (
          <>
            {" "}
            · {item.status} by {item.decided_by}
            {item.decided_at && <> on {formatDate(item.decided_at)}</>}
          </>
        )}
      </p>
      {item.overdue && (
        <p className="warning">Waiting longer than 3 sessions. The nightly check warns until it is decided.</p>
      )}
      <p className="reason">{item.reason}</p>
      {item.note && <p className="note">Note: {item.note}</p>}
    </header>
  );
}
