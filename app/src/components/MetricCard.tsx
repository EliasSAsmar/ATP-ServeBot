import type { ElbowBand, MetricValue } from "../types/api";
import { Panel } from "./Terminal";

/**
 * Elbow-angle metric panel (UI.md §5.2, §6). Product stance (OVERVIEW.md §5):
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

/** Gauge fill tone per band — color is never the only signal (text tag too). */
const BAND_TONE: Record<ElbowBand, "good" | "warn" | "bad"> = {
  straight: "good",
  nearly_straight: "good",
  slightly_bent: "warn",
  bent: "bad",
  very_bent: "bad",
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

/** ASCII target-band gauge: filled blocks to the value, ▓ target band. */
function AsciiGauge({ value, refLo, refHi }: { value: number; refLo: number; refHi: number }) {
  const total = 24;
  const cells: Array<{ ch: string; cls: string }> = [];
  const fillCount = Math.round((Math.min(180, Math.max(0, value)) / 180) * total);
  const targetStart = Math.floor((refLo / 180) * total);
  const targetEnd = Math.ceil((refHi / 180) * total);
  for (let i = 0; i < total; i++) {
    const inTarget = i >= targetStart && i < targetEnd;
    const filled = i < fillCount;
    if (filled) cells.push({ ch: "█", cls: "fill" });
    else if (inTarget) cells.push({ ch: "▓", cls: "target" });
    else cells.push({ ch: "░", cls: "empty" });
  }
  // Group consecutive cells of the same class into single spans.
  const runs: Array<{ text: string; cls: string }> = [];
  for (const c of cells) {
    const last = runs[runs.length - 1];
    if (last && last.cls === c.cls) last.text += c.ch;
    else runs.push({ text: c.ch, cls: c.cls });
  }
  return (
    <div className="gbar" aria-hidden="true">
      {runs.map((r, i) => (
        <span key={i} className={r.cls}>
          {r.text}
        </span>
      ))}
    </div>
  );
}

export function MetricCard({ metric }: { metric: MetricValue }) {
  const side = metric.side === "left" ? "left" : "right";

  // Implemented-but-failed on this clip → metric_unavailable state (UI.md §5).
  if (metric.value === null) {
    return (
      <Panel label="elbow_angle" meta={`${side} · at contact`} ariaLabel="Elbow angle at contact">
        <p className="metric-unavailable" style={{ marginTop: 0 }}>
          Couldn&rsquo;t measure this angle on this serve.
        </p>
        <p className="muted small">
          Try again with your whole body clearly in frame and good lighting.
        </p>
      </Panel>
    );
  }

  const uncertain = metric.confidence < MIN_TIP_CONFIDENCE;
  const [refLo, refHi] = metric.reference_range_deg ?? [150, 180];
  const clamped = Math.min(180, Math.max(0, metric.value));
  const display = formatDegrees(metric.value); // "~118°"
  const digits = display.slice(1, -1);
  const tone = metric.band ? BAND_TONE[metric.band] : "warn";

  return (
    <Panel label="elbow_angle" meta={`${side} · at contact`} ariaLabel="Elbow angle at contact">
      <div className="metric-top">
        <span className="key">degrees · {side} arm · AI estimate</span>
        <span className={`conf ${uncertain ? "conf-low" : ""}`}>
          confidence: {confidenceLabel(metric.confidence)}
        </span>
      </div>

      <div className="readline">
        <span className={`bignum ${uncertain ? "metric-dim" : ""}`}>
          <span className="approx">~</span>
          {digits}
          <span className="deg">°</span>
        </span>
        {metric.band && !uncertain ? (
          <span className={`band band-${metric.band}`}>{BAND_LABELS[metric.band]}</span>
        ) : null}
      </div>

      {/* Reference-range gauge: 0–180°, target band highlighted, fill to value */}
      <div
        className={`gauge ${tone === "good" ? "gauge-good" : tone === "bad" ? "gauge-bad" : ""}`}
        role="img"
        aria-label={`Estimated ${display}; reference range ${refLo} to ${refHi} degrees`}
      >
        <AsciiGauge value={clamped} refLo={refLo} refHi={refHi} />
        <div className="gscale">
          <span>0°</span>
          <span className="good">
            reference {refLo.toFixed(0)}–{refHi.toFixed(0)}°
          </span>
          <span>180°</span>
        </div>
      </div>

      {uncertain ? (
        <p className="uncertain-note">
          The AI wasn&rsquo;t confident about your form on this serve — try again with your whole
          body clearly in frame and good lighting.
        </p>
      ) : null}
    </Panel>
  );
}
