import type { Tip } from "../types/api";

/** Tip cards styled by severity (UI.md §5.3); empty tips[] → neutral message. */
export function TipList({ tips, suppressed }: { tips: Tip[]; suppressed?: boolean }) {
  if (suppressed) return null; // uncertain state — no corrective tips (UI.md §6)
  if (tips.length === 0) {
    return (
      <section className="card tip-card tip-neutral">
        <h3>Coaching</h3>
        <p>Looking solid on this one.</p>
      </section>
    );
  }
  return (
    <>
      {tips.map((tip) => (
        <section key={tip.id} className={`card tip-card tip-${tip.severity}`}>
          <header className="tip-head">
            <h3>{tip.title}</h3>
            <span className={`chip chip-severity-${tip.severity}`}>{tip.severity}</span>
          </header>
          <p>{tip.message}</p>
        </section>
      ))}
    </>
  );
}
