import type {
  BallTrackPoint,
  ContactHeightMetric,
  KineticChainSequence,
  PhaseTimingMetric,
  ServeTracking,
  TossPlacement,
} from "../types/api";
import { Panel } from "./Terminal";

/**
 * Phase-2 result panels: contact height, phase timeline, kinetic-chain timing
 * chart, toss-placement map and ball-flight sparkline. All values are AI
 * estimates inferred from a single camera — every panel renders only what the
 * backend actually returned (no field is ever fabricated), and each component
 * returns null / an "unavailable" state when its data is missing.
 */

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

// ---------------------------------------------------------------------------
// # contact_height — ratio + meters
// ---------------------------------------------------------------------------

export function ContactHeightPanel({ metric }: { metric: ContactHeightMetric }) {
  if (!num(metric.value)) {
    return (
      <Panel label="contact_height" meta="wrist ÷ standing height" ariaLabel="Contact height">
        <p className="metric-unavailable" style={{ marginTop: 0 }}>
          Couldn&rsquo;t estimate contact height on this serve.
        </p>
      </Panel>
    );
  }
  return (
    <Panel label="contact_height" meta="wrist ÷ standing height" ariaLabel="Contact height">
      <div className="metric-top">
        <span className="key">ratio · AI estimate</span>
      </div>
      <div className="readline">
        <span className="bignum">
          <span className="approx">~</span>
          {metric.value.toFixed(2)}
          <span className="deg">×</span>
        </span>
        <span className="ch-detail">
          {num(metric.wrist_y_m) ? (
            <span className="block">
              wrist at contact <b>~{metric.wrist_y_m.toFixed(2)} m</b>
            </span>
          ) : null}
          {num(metric.standing_height_m) ? (
            <span className="block">
              standing height <b>~{metric.standing_height_m.toFixed(2)} m</b>
            </span>
          ) : null}
        </span>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// # phase_timing — proportional horizontal timeline
// ---------------------------------------------------------------------------

const PHASE_ORDER = ["windup", "trophy", "acceleration", "follow_through"] as const;
const PHASE_SHORT: Record<string, string> = {
  windup: "windup",
  trophy: "trophy",
  acceleration: "accel",
  follow_through: "follow",
};

export function PhaseTimelinePanel({ timing }: { timing: PhaseTimingMetric }) {
  const phases = PHASE_ORDER.map((key) => ({ key, ms: timing.phases?.[key] })).filter(
    (p): p is { key: (typeof PHASE_ORDER)[number]; ms: number } => num(p.ms) && p.ms > 0,
  );
  if (phases.length === 0) return null;
  const total = phases.reduce((s, p) => s + p.ms, 0);

  return (
    <Panel
      label="phase_timing"
      meta={num(timing.contact_ms) ? `contact @ ${Math.round(timing.contact_ms)}ms` : undefined}
      ariaLabel="Serve phase timing"
    >
      <div
        className="pt-bar"
        role="img"
        aria-label={`Phase durations: ${phases.map((p) => `${p.key} ${Math.round(p.ms)} milliseconds`).join(", ")}`}
      >
        {phases.map((p) => (
          <div
            key={p.key}
            className={`pt-seg ${p.key === "acceleration" ? "pt-accel" : ""}`}
            style={{ flex: `${p.ms} 1 0px` }}
            title={`${p.key}: ${Math.round(p.ms)}ms`}
          >
            <span className="pt-name">{PHASE_SHORT[p.key] ?? p.key}</span>
            <span className="pt-ms">{Math.round(p.ms)}ms</span>
          </div>
        ))}
      </div>
      <div className="pt-scale">
        <span>0ms</span>
        <span className="good">acceleration highlighted</span>
        <span>~{Math.round(total)}ms</span>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// # kinetic_chain — per-segment peak-velocity timing chart
// ---------------------------------------------------------------------------

const DEFAULT_SEGMENTS = ["pelvis", "trunk", "upper_arm", "forearm", "hand"];

export function KineticChainPanel({
  chain,
  contactMs,
}: {
  chain: KineticChainSequence;
  contactMs?: number;
}) {
  const names = chain.segments?.length ? chain.segments : DEFAULT_SEGMENTS;
  const segs = names
    .map((name) => ({ name, t: chain.peak_times_ms?.[name], v: chain.peak_deg_s?.[name] }))
    .filter((s) => num(s.t)); // runtime guard: records may miss segments
  if (segs.length === 0) return null;

  const contactT = num(contactMs) ? contactMs : null;
  const firstT = Math.min(...segs.map((s) => s.t));
  let lo = firstT;
  let hi = Math.max(...segs.map((s) => s.t));
  if (contactT !== null) hi = Math.max(hi, contactT);
  const pad = Math.max(10, (hi - lo) * 0.08);
  lo -= pad;
  hi += pad;
  const pct = (t: number) => ((t - lo) / (hi - lo)) * 100;
  const rel = (t: number) => {
    if (contactT === null) return `${Math.round(t)}ms`;
    const d = Math.round(t - contactT);
    return `${d > 0 ? "+" : ""}${d}ms`;
  };

  return (
    <Panel label="kinetic_chain" meta="peak segment speed · timing" ariaLabel="Kinetic chain sequence">
      <div className={`kc-status ${chain.order_correct ? "kc-ok" : "kc-warn"}`}>
        {chain.order_correct ? "✓" : "✗"} {segs.map((s) => s.name).join(" → ")}
        {chain.order_correct ? " · proximal → distal" : " · out of order"}
      </div>
      {chain.gaps_ms?.length ? (
        <div className="kc-gaps">peak-to-peak gaps: {chain.gaps_ms.map((g) => Math.round(g)).join(" / ")}ms</div>
      ) : null}

      <div className="kc-rows" role="img" aria-label="Peak angular velocity time per body segment, relative to contact">
        {segs.map((s) => {
          const p = pct(s.t);
          return (
            <div className="kc-row" key={s.name}>
              <span className="kc-name">{s.name}</span>
              <div className="kc-track">
                {contactT !== null ? (
                  <span className="kc-contact" style={{ left: `${pct(contactT)}%` }} aria-hidden="true" />
                ) : null}
                <span
                  className="kc-dot"
                  style={{ left: `${p}%` }}
                  title={`${s.name} peak ${rel(s.t)}${contactT !== null ? " vs contact" : ""}`}
                />
                <span className={`kc-t ${p > 62 ? "kc-t-left" : ""}`} style={{ left: `${p}%` }}>
                  {rel(s.t)}
                </span>
              </div>
              <span className="kc-val">{num(s.v) ? `~${Math.round(s.v)}°/s` : "—"}</span>
            </div>
          );
        })}
      </div>

      <div className="kc-scale">
        <span>{rel(firstT)}</span>
        <span>{contactT !== null ? "contact (0ms) ┊" : ""}</span>
      </div>
      {chain.note ? <p className="kc-note">// {chain.note}</p> : null}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// tracking helpers — convert pixel tracks to meters anchored at the ball apex
// ---------------------------------------------------------------------------

interface BallSample {
  t: number;
  h: number; // meters above ground (apex-anchored estimate)
}

function ballHeightSeries(tracking: ServeTracking): BallSample[] {
  const ball = tracking.ball;
  const ppm = tracking.scale?.px_per_m;
  if (!ball?.points?.length || !ball.apex || !num(ball.apex.height_m) || !num(ppm) || ppm <= 0) return [];
  const flight = ball.points.filter((p) => p.in_flight);
  const pts: BallTrackPoint[] = flight.length >= 2 ? flight : ball.points;
  if (pts.length < 2) return [];
  // Anchor pixel→meter conversion at the sample nearest the reported apex.
  const anchor = pts.reduce((best, p) =>
    Math.abs(p.t_ms - ball.apex.t_ms) < Math.abs(best.t_ms - ball.apex.t_ms) ? p : best,
  );
  return pts
    .map((p) => ({ t: p.t_ms, h: ball.apex.height_m - (p.y - anchor.y) / ppm }))
    .filter((p) => num(p.t) && num(p.h))
    .sort((a, b) => a.t - b.t);
}

// ---------------------------------------------------------------------------
// # toss — placement map (side view: apex height vs forward offset)
// ---------------------------------------------------------------------------

export function TossPanel({
  toss,
  tracking,
}: {
  toss: TossPlacement;
  tracking: ServeTracking | null;
}) {
  const fwd = num(toss.offset_forward_cm) ? toss.offset_forward_cm / 100 : null;
  const apex = num(toss.apex_height_m) ? toss.apex_height_m : null;
  if (fwd === null && apex === null) return null;

  const W = 300;
  const H = 158;
  const padL = 30;
  const padR = 14;
  const padT = 16;
  const padB = 20;
  const xMin = -0.5;
  const xMax = Math.max(1.0, (fwd ?? 0.3) + 0.4);
  const yMax = Math.max(3, (apex ?? 2.6) + 0.4);
  const X = (m: number) => padL + ((m - xMin) / (xMax - xMin)) * (W - padL - padR);
  const Y = (m: number) => H - padB - (m / yMax) * (H - padT - padB);

  // Optional real ball arc, anchored at the apex (derived from tracking, in m).
  let arc: string | null = null;
  const ball = tracking?.ball;
  const ppm = tracking?.scale?.px_per_m;
  if (ball?.points?.length && num(ppm) && ppm > 0 && fwd !== null && apex !== null && ball.apex) {
    const flight = ball.points.filter((p) => p.in_flight);
    if (flight.length >= 2) {
      const anchor = flight.reduce((best, p) =>
        Math.abs(p.t_ms - ball.apex.t_ms) < Math.abs(best.t_ms - ball.apex.t_ms) ? p : best,
      );
      const pts = flight
        .map((p) => ({ xm: fwd + (p.x - anchor.x) / ppm, ym: apex - (p.y - anchor.y) / ppm }))
        .filter((p) => num(p.xm) && num(p.ym) && p.ym >= 0 && p.ym <= yMax && p.xm >= xMin && p.xm <= xMax);
      if (pts.length >= 2) arc = pts.map((p) => `${X(p.xm).toFixed(1)},${Y(p.ym).toFixed(1)}`).join(" ");
    }
  }

  const lat = toss.offset_lateral_cm;
  return (
    <Panel
      label="toss"
      meta={toss.reference ? `vs ${toss.reference.replace(/_/g, " ")}` : undefined}
      ariaLabel="Toss placement"
    >
      <svg
        className="viz-svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Toss apex ${apex !== null ? `about ${apex.toFixed(2)} meters high` : "unknown height"}${
          fwd !== null ? `, about ${Math.round(fwd * 100)} centimeters forward of the reference` : ""
        }`}
      >
        {/* height gridlines */}
        {[1, 2, 3].map((m) =>
          m <= yMax ? (
            <g key={m}>
              <line x1={padL} y1={Y(m)} x2={W - padR} y2={Y(m)} className="viz-grid" />
              <text x={padL - 5} y={Y(m) + 3} className="viz-txt" textAnchor="end">
                {m}m
              </text>
            </g>
          ) : null,
        )}
        {/* ground + body reference line */}
        <line x1={padL - 14} y1={Y(0)} x2={W - padR} y2={Y(0)} className="viz-ground" />
        <line x1={X(0)} y1={Y(0)} x2={X(0)} y2={padT} className="viz-ref" />
        <text x={X(0)} y={H - 6} className="viz-txt" textAnchor="middle">
          0
        </text>
        {arc ? <polyline points={arc} className="viz-arc" /> : null}
        {fwd !== null && apex !== null ? (
          <g>
            <line x1={X(fwd)} y1={Y(0)} x2={X(fwd)} y2={Y(apex)} className="viz-drop" />
            <circle cx={X(fwd)} cy={Y(apex)} r={3.4} className="viz-apex" />
            <text
              x={Math.min(X(fwd) + 7, W - 72)}
              y={Math.max(Y(apex) - 6, 10)}
              className="viz-txt viz-txt-green"
            >
              apex ~{apex.toFixed(1)}m
            </text>
          </g>
        ) : null}
      </svg>
      <div className="viz-readout">
        <span>
          <b>forward</b> {fwd !== null ? `${toss.offset_forward_cm >= 0 ? "+" : ""}${Math.round(toss.offset_forward_cm)}cm` : "—"}
        </span>
        <span>
          <b>lateral</b> {num(lat) ? `${lat >= 0 ? "+" : ""}${Math.round(lat)}cm` : "—"}
        </span>
        <span>
          <b>apex</b> {apex !== null ? `~${apex.toFixed(2)}m` : "—"}
        </span>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// # ball_flight — height-vs-time sparkline with apex + contact marked
// ---------------------------------------------------------------------------

export function BallTrajectoryPanel({ tracking }: { tracking: ServeTracking }) {
  const series = ballHeightSeries(tracking);
  if (series.length < 2) return null;

  const apex = tracking.ball.apex;
  const contact = tracking.contact;
  const hasContact = contact && num(contact.t_ms) && num(contact.height_m);

  const t0 = series[0].t;
  let t1 = series[series.length - 1].t;
  if (hasContact) t1 = Math.max(t1, contact.t_ms);
  if (t1 <= t0) return null;

  let hMin = Math.min(...series.map((p) => p.h));
  let hMax = Math.max(...series.map((p) => p.h), apex.height_m);
  if (hasContact) hMax = Math.max(hMax, contact.height_m);
  const hPad = Math.max(0.15, (hMax - hMin) * 0.15);
  hMin -= hPad;
  hMax += hPad;

  const W = 480;
  const H = 120;
  const padX = 10;
  const padY = 12;
  const X = (t: number) => padX + ((t - t0) / (t1 - t0)) * (W - padX * 2);
  const Y = (h: number) => H - padY - ((h - hMin) / (hMax - hMin)) * (H - padY * 2);

  const line = series.map((p) => `${X(p.t).toFixed(1)},${Y(p.h).toFixed(1)}`).join(" ");
  const peakSpeed = tracking.racket?.peak_speed_m_s;

  return (
    <Panel
      label="ball_flight"
      meta={num(peakSpeed) ? `racket peak ~${peakSpeed.toFixed(1)} m/s` : "height vs time"}
      ariaLabel="Ball flight height over time"
    >
      <svg
        className="viz-svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Ball height over time; apex about ${apex.height_m.toFixed(2)} meters${
          hasContact ? `, contact about ${contact.height_m.toFixed(2)} meters` : ""
        }`}
      >
        <polyline points={line} className="viz-line" />
        {/* apex */}
        {num(apex.t_ms) && num(apex.height_m) ? (
          <g>
            <circle cx={X(apex.t_ms)} cy={Y(apex.height_m)} r={3.4} className="viz-apex" />
            <text
              x={Math.min(X(apex.t_ms), W - 78)}
              y={Math.max(Y(apex.height_m) - 7, 9)}
              className="viz-txt viz-txt-green"
              textAnchor="middle"
            >
              apex ~{apex.height_m.toFixed(2)}m
            </text>
          </g>
        ) : null}
        {/* contact */}
        {hasContact ? (
          <g>
            <line
              x1={X(contact.t_ms) - 4}
              y1={Y(contact.height_m) - 4}
              x2={X(contact.t_ms) + 4}
              y2={Y(contact.height_m) + 4}
              className="viz-contact"
            />
            <line
              x1={X(contact.t_ms) - 4}
              y1={Y(contact.height_m) + 4}
              x2={X(contact.t_ms) + 4}
              y2={Y(contact.height_m) - 4}
              className="viz-contact"
            />
            <text
              x={Math.min(X(contact.t_ms) - 8, W - 12)}
              y={Math.min(Y(contact.height_m) + 16, H - 4)}
              className="viz-txt viz-txt-cyan"
              textAnchor="end"
            >
              contact ~{contact.height_m.toFixed(2)}m
            </text>
          </g>
        ) : null}
      </svg>
      <div className="gscale">
        <span>{Math.round(t0)}ms</span>
        <span>clip time</span>
        <span>{Math.round(t1)}ms</span>
      </div>
    </Panel>
  );
}
