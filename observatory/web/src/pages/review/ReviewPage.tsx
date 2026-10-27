// The Review page (spec 0005, section 4). The queue and item views arrive in slice O2,
// once spec 0002's review tables exist.
export function ReviewPage() {
  return (
    <div className="review">
      <aside className="queue">
        <h2>Waiting</h2>
        <p className="empty">Nothing is waiting for review.</p>
      </aside>
      <section className="item">
        <p className="empty">Pick an item on the left to see why it is waiting.</p>
      </section>
    </div>
  );
}
