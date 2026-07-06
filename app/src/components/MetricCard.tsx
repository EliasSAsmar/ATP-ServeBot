import type { AngleMetric } from "../types/api";
import { Panel } from "./Terminal";

/**
 * Angle metric panel (UI.md §5.2, §6) — elbow by default, reused for the
 * phase-2 shoulder / knee metrics. Product stance (OVERVIEW.md §5): values
 * are AI estimates — always `~` + ≤1 decimal, band language stays
 * directional/qualitative, never clinical.
 */

const MIN_TIP_CONFIDENCE = 0.5; // METRICS.md §9.3

const BAND_LABELS: Record<string, string> = {
  straight: "Straight",
  nearly_straight: "Nearly straight",
  slightly_bent: "Slightly bent",
  bent: "Bent",
  very_bent: "Very bent",
};

/** Gauge fill tone per band — color is never the only signal (text tag too). */
const BAND_TONE: Record<string, "good" | "warn" | "bad"> = {
  straight: "good",
  nearly_straight: "good",
  slightly_bent: "warn",
  bent: "bad",
  very_bent: "bad",
  // phase-2 band vocab (shoulder / knee)
  well_elevated: "good",
  slightly_low: "warn",
  low: "bad",
  good_bend: "good",
  shallow_bend: "warn",
  deep_bend: "warn",
};

/** Unknown band strings from the backend render as humanized text. */
function bandLabel(band: string): string {
  if (BAND_LABELS[band]) return BAND_LABELS[band];
  const s = band.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function bandTone(
  band: string | undefined,
  value: number,
  ref?: [number, number],
): "good" | "warn" | "bad" | "neutral" {
  if (band && BAND_TONE[band]) return BAND_TONE[band];
  if (ref) return value >= ref[0] && value <= ref[1] ? "good" : "warn";
  return "neutral";
}

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

/** ASCII target-band gauge: filled blocks to the value, ▓ target band (if any). */
function AsciiGauge({
  value,
  refLo,
  refHi,
  max,
}: {
  value: number;
  refLo?: number;
  refHi?: number;
  max: number;
}) {
  const total = 24;
  const cells: Array<{ ch: string; cls: string }> = [];
  const fillCount = Math.round((Math.min(max, Math.max(0, value)) / max) * total);
  const hasTarget = refLo !== undefined && refHi !== undefined;
  const targetStart = hasTarget ? Math.floor((refLo / max) * total) : -1;
  const targetEnd = hasTarget ? Math.ceil((refHi / max) * total) : -1;
  for (let i = 0; i < total; i++) {
    const inTarget = hasTarget && i >= targetStart && i < targetEnd;
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

export function MetricCard({
  metric,
  label = "elbow_angle",
  subject = "arm",
  meta,
  maxDeg = 180,
}: {
  metric: AngleMetric;
  /** Panel `#` label, e.g. "shoulder_angle". */
  label?: string;
  /** Body part for the key line, e.g. "arm" / "leg". */
  subject?: string;
  /** Panel meta text; defaults to `{side} · at contact`. */
  meta?: string;
  /** Gauge scale ceiling (180 for arm angles, 90 for knee flexion). */
  maxDeg?: number;
}) {
  const side = metric.side === "left" ? "left" : "right";
  const panelMeta = meta ?? `${side} · at contact`;
  const aria = `${label.replace(/_/g, " ")} (${side})`;

  // Implemented-but-failed on this clip → metric_unavailable state (UI.md §5).
  if (metric.value === null || metric.value === undefined || !Number.isFinite(metric.value)) {
    return (
      <Panel label={label} meta={panelMeta} ariaLabel={aria}>
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
  const ref = metric.reference_range_deg;
  const clamped = Math.min(maxDeg, Math.max(0, metric.value));
  const display = formatDegrees(metric.value); // "~118°"
  const digits = display.slice(1, -1);
  const tone = bandTone(metric.band, metric.value, ref);

  return (
    <Panel label={label} meta={panelMeta} ariaLabel={aria}>
      <div className="metric-top">
        <span className="key">
          degrees · {side} {subject} · AI estimate
        </span>
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
          <span className={`band band-${metric.band} band-t-${tone}`}>{bandLabel(metric.band)}</span>
        ) : null}
      </div>

      {/* Reference-range gauge: 0–max°, target band highlighted, fill to value */}
      <div
        className={`gauge ${tone === "good" ? "gauge-good" : tone === "bad" ? "gauge-bad" : ""}`}
        role="img"
        aria-label={
          ref
            ? `Estimated ${display}; reference range ${ref[0]} to ${ref[1]} degrees`
            : `Estimated ${display} on a 0 to ${maxDeg} degree scale`
        }
      >
        <AsciiGauge value={clamped} refLo={ref?.[0]} refHi={ref?.[1]} max={maxDeg} />
        <div className="gscale">
          <span>0°</span>
          {ref ? (
            <span className="good">
              reference {ref[0].toFixed(0)}–{ref[1].toFixed(0)}°
            </span>
          ) : null}
          <span>{maxDeg}°</span>
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
