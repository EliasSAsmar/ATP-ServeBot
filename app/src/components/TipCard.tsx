import type { Tip } from "../types/api";
import { Panel } from "./Terminal";

/** Coach panels styled by severity (UI.md §5.3); empty tips[] → neutral message. */
export function TipList({ tips, suppressed }: { tips: Tip[]; suppressed?: boolean }) {
  if (suppressed) return null; // uncertain state — no corrective tips (UI.md §6)
  if (tips.length === 0) {
    return (
      <Panel label="coach" className="coach" ariaLabel="Coaching">
        <p>
          <span className="arrow">&gt;</span> Looking solid on this one.
        </p>
      </Panel>
    );
  }
  return (
    <>
      {tips.map((tip) => (
        <Panel
          key={tip.id}
          label="coach"
          meta={tip.metric}
          className="coach"
          ariaLabel={`Coaching tip: ${tip.title}`}
        >
          <header className="tip-head">
            <h3>{tip.title}</h3>
            {/* severity is always spelled out — color is never the only signal */}
            <span className={`sev sev-${tip.severity}`}>{tip.severity}</span>
          </header>
          <p>
            <span className="arrow">&gt;</span> {tip.message}
          </p>
        </Panel>
      ))}
    </>
  );
}
