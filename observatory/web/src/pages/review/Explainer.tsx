// How to decide an item of this kind, in the API's words (cagr_observatory/review/reasons.py).
export function Explainer({ guide }: { guide: string }) {
  return (
    <aside className="explainer">
      <h3>How to decide</h3>
      <p>{guide}</p>
    </aside>
  );
}
