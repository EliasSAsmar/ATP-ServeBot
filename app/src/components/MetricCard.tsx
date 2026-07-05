import type { ElbowBand, MetricValue } from "../types/api";

/**
 * Elbow-angle metric card (UI.md §5.2, §6). Product stance (OVERVIEW.md §5):
 * values are AI estimates — always `~` + ≤1 decimal, band language stays
 * directional/qualitative, never clinical.
 */

const MIN_TIP_CONFIDENCE = 0.5; // METRICS.md §9.3

const BAND_LABELS: Record<ElbowBand, string> = {
  straight: "Straight",
  nearly_straight: "Nearly straight",
  slightly_bent: "Slightly bent",
  bent: "Bent",
  very_bent: "Very bent",
};

/** `~{value}°` with at most one decimal (trailing .0 trimmed). */
export function formatDegrees(value: number): string {
  const v = Math.round(value * 10) / 10;
  return `~${Number.isInteger(v) ? v.toFixed(0) : v.toFixed(1)}°`;
}

function confidenceLabel(c: number): string {
  if (c < MIN_TIP_CONFIDENCE) return "low";
  if (c < 0.75) return "medium";
  return "high";
}

export function MetricCard({ metric }: { metric: MetricValue }) {
  // Implemented-but-failed on this clip → metric_unavailable state (UI.md §5).
  if (metric.value === null) {
    return (
      <section className="card metric-card" aria-label="Elbow angle at contact">
        <header className="metric-head">
          <h3>Elbow angle at contact</h3>
        </header>
        <p className="metric-unavailable">Couldn&rsquo;t measure this angle on this serve.</p>
        <p className="muted small">
          Try again with your whole body clearly in frame and good lighting.
        </p>
      </section>
    );
  }

  const uncertain = metric.confidence < MIN_TIP_CONFIDENCE;
  const [refLo, refHi] = metric.reference_range_deg ?? [150, 180];
  const clamped = Math.min(180, Math.max(0, metric.value));

  return (
    <section className="card metric-card" aria-label="Elbow angle at contact">
      <header className="metric-head">
        <h3>Elbow angle at contact</h3>
        <span className="muted small">
          {metric.side === "left" ? "left" : "right"} arm &middot; AI estimate
        </span>
      </header>

      <div className={`metric-value ${uncertain ? "metric-dim" : ""}`}>
        {formatDegrees(metric.value)}
      </div>

      {metric.band && !uncertain ? (
        <div className={`band-label band-${metric.band}`}>{BAND_LABELS[metric.band]}</div>
      ) : null}

      {/* Reference-range gauge: 0–180°, "good" band highlighted, marker at value */}
      <div
        className="gauge"
        role="img"
        aria-label={`Estimated ${formatDegrees(metric.value)}; reference range ${refLo} to ${refHi} degrees`}
      >
        <div className="gauge-track">
          <div
            className="gauge-reference"
            style={{ left: `${(refLo / 180) * 100}%`, width: `${((refHi - refLo) / 180) * 100}%` }}
          />
          <div className="gauge-marker" style={{ left: `${(clamped / 180) * 100}%` }} />
        </div>
        <div className="gauge-scale muted small">
          <span>0°</span>
          <span>
            reference {refLo.toFixed(0)}–{refHi.toFixed(0)}°
          </span>
          <span>180°</span>
        </div>
      </div>

      <p className="muted small">estimate confidence: {confidenceLabel(metric.confidence)}</p>

      {uncertain ? (
        <p className="uncertain-note">
          The AI wasn&rsquo;t confident about your form on this serve — try again with your whole
          body clearly in frame and good lighting.
        </p>
      ) : null}
    </section>
  );
}
