import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { approve, getHealth, type Item, type Listing, reject } from "../../api/client";

type Props = {
  item: Item;
  approveLabel: string;
  /** What approving and rejecting write, in words. */
  approveEffect?: string;
  rejectEffect?: string;
  /** For a possible index rename: the listings it may rename, one of which approval needs. */
  choices?: Listing[];
  onDecided: (item: Item) => void;
};

// Approve or reject (spec 0005, section 4.3). A note is optional to approve and required to
// reject, so the history explains itself. Keys: a approves, r rejects (or moves to the note).
export function Decision({ item, approveLabel, approveEffect, rejectEffect, choices, onDecided }: Props) {
  const queryClient = useQueryClient();
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const [note, setNote] = useState("");
  const [intoId, setIntoId] = useState<number | null>(null);
  const noteBox = useRef<HTMLTextAreaElement>(null);
  const [askNote, setAskNote] = useState(false);

  const decide = useMutation({
    mutationFn: (action: "approve" | "reject") =>
      action === "approve"
        ? approve(item.kind, item.id, { note: note.trim() || null, into_id: intoId })
        : reject(item.kind, item.id, note.trim()),
    onSuccess: (decided) => {
      queryClient.invalidateQueries({ queryKey: ["review"] });
      onDecided(decided);
    },
  });

  const needsChoice = Boolean(choices?.length) && intoId === null;
  const canApprove = !decide.isPending && !needsChoice;
  const canReject = !decide.isPending && note.trim().length > 0;

  // A rejection needs a reason. Without one, Reject points to the note box instead of doing
  // nothing.
  function tryReject() {
    if (canReject) decide.mutate("reject");
    else if (!decide.isPending) {
      setAskNote(true);
      noteBox.current?.focus();
    }
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target;
      if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) return;
      if (target instanceof HTMLSelectElement) return;
      if (event.key === "a" && canApprove) decide.mutate("approve");
      if (event.key === "r") {
        event.preventDefault();
        tryReject();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (item.status !== "pending") return null;
  if (health && !health.reviewer) {
    return (
      <p className="hint decision">
        This Observatory only reads. Set CAGR_REVIEWER and CAGR_REVIEW_DATABASE_URL to decide here.
      </p>
    );
  }
  return (
    <form className="decision" onSubmit={(e) => e.preventDefault()}>
      {choices && choices.length > 0 && (
        <fieldset>
          <legend>Which index does it rename?</legend>
          {choices.map((l) => (
            <label key={l.listing_id}>
              <input
                type="radio"
                name="into"
                checked={intoId === l.listing_id}
                onChange={() => setIntoId(l.listing_id)}
              />{" "}
              {l.symbol ?? `Listing ${l.listing_id}`}
            </label>
          ))}
        </fieldset>
      )}
      {(approveEffect || rejectEffect) && (
        <dl className="effects">
          {approveEffect && (
            <>
              <dt>Approve</dt>
              <dd>{approveEffect}</dd>
            </>
          )}
          {rejectEffect && (
            <>
              <dt>Reject</dt>
              <dd>{rejectEffect}</dd>
            </>
          )}
        </dl>
      )}
      <label className="note-field">
        Note {health?.reviewer && <span className="hint">(deciding as {health.reviewer})</span>}
        <textarea
          ref={noteBox}
          rows={2}
          value={note}
          placeholder="Optional to approve; needed to reject"
          aria-invalid={askNote && !note.trim()}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <div className="buttons">
        <button type="button" className="approve" disabled={!canApprove} onClick={() => decide.mutate("approve")}>
          {approveLabel}
        </button>
        <button type="button" className="reject" disabled={decide.isPending} onClick={tryReject}>
          Reject
        </button>
      </div>
      {askNote && !note.trim() && <p className="error">Write in the note why you reject it, then press Reject again.</p>}
      {decide.isError && <p className="error">{decide.error.message}</p>}
    </form>
  );
}
