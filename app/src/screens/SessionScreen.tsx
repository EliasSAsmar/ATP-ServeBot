import { useSyncExternalStore } from "react";
import { Cursor, Panel, TermWindow } from "../components/Terminal";
import type { Settings } from "../config";
import { practiceQueue, summarize, type PracticeItem } from "../flow/practiceQueue";
import type { JobResponse } from "../types/api";

/**
 * Practice session review: every captured serve/swing with its analysis
 * status, updating live while the queue drains in the background. Succeeded
 * items open the full Result screen.
 */

function timeLabel(ms: number): string {
  const d = new Date(ms);
  return d.toTimeString().slice(0, 8);
}

function statusOf(item: PracticeItem): { icon: string; cls: string; text: string } {
  const p = item.phase;
  switch (p.kind) {
    case "waiting":
      return { icon: "·", cls: "muted", text: "waiting" };
    case "uploading":
      return { icon: "▸", cls: "run", text: `uploading ${Math.round(p.progress * 100)}%` };
    case "queued":
      return { icon: "▸", cls: "run", text: "queued" };
    case "running":
      return { icon: "▸", cls: "run", text: p.stage ?? "analyzing" };
    case "succeeded":
      return { icon: "✓", cls: "ok", text: "done" };
    case "cancelled":
      return { icon: "✗", cls: "err", text: "cancelled" };
    case "failed":
      return { icon: "✗", cls: "err", text: p.code };
  }
}

function metricLabel(item: PracticeItem): string {
  if (item.phase.kind !== "succeeded" || !item.job?.result) return "";
  if (item.sport === "golf") return "3D scan";
  const elbow = item.job.result.metrics.elbow_angle_deg;
  return elbow && elbow.value !== null ? `elbow ~${Math.round(elbow.value)}°` : "3D scan";
}

export function SessionScreen({
  settings,
  onOpenResult,
  onBackToLive,
}: {
  settings: Settings;
  onOpenResult: (job: JobResponse) => void;
  onBackToLive: () => void;
}) {
  const items = useSyncExternalStore(practiceQueue.subscribe, practiceQueue.getSnapshot);
  const s = summarize(items);
  const golf = settings.sport === "golf";

  return (
    <div className="stage session-screen">
      <TermWindow context="~/session" className="result-win">
        <div className="cmd">
          <span className="prompt">$</span> servebot session{" "}
          <span className="flag">--list</span>
        </div>

        <div className="log" aria-live="polite">
          <span>
            <span className="ok">✓</span> captured{" "}
            <span className="val">
              {s.total} {golf ? "swing" : "serve"}
              {s.total === 1 ? "" : "s"}
            </span>
          </span>
          <span>
            {s.active + s.waiting > 0 ? (
              <>
                <span className="run">▸</span> processing{" "}
                <span className="val">
                  {s.done} done · {s.active} analyzing · {s.waiting} waiting
                  {s.failed > 0 ? ` · ${s.failed} failed` : ""}
                </span>{" "}
                <Cursor />
              </>
            ) : (
              <>
                <span className="ok">✓</span> processing{" "}
                <span className="val">
                  {s.done} done{s.failed > 0 ? ` · ${s.failed} failed` : ""}
                </span>
              </>
            )}
          </span>
        </div>

        {items.length === 0 ? (
          <Panel label="session" ariaLabel="Practice session">
            <p className="muted small" style={{ margin: 0 }}>
              No captures yet. Turn on Practice in the live view and fire away — every{" "}
              {golf ? "swing" : "serve"} lands here.
            </p>
          </Panel>
        ) : (
          <Panel label="captures" meta="newest last" ariaLabel="Captured clips">
            <ul className="session-list">
              {items.map((item) => {
                const st = statusOf(item);
                const metric = metricLabel(item);
                return (
                  <li key={item.id} className="session-row">
                    <span className="session-seq">#{item.seq}</span>
                    <span className="muted session-time">{timeLabel(item.capturedAt)}</span>
                    <span className="session-sport">{item.sport}</span>
                    <span className={`session-status ${st.cls}`}>
                      {st.icon} {st.text}
                    </span>
                    <span className="session-metric">{metric}</span>
                    {item.phase.kind === "succeeded" && item.job ? (
                      <button className="btn btn-ghost session-open" onClick={() => onOpenResult(item.job!)}>
                        view
                      </button>
                    ) : (
                      <span className="session-open" />
                    )}
                  </li>
                );
              })}
            </ul>
          </Panel>
        )}

        <div className="actions">
          <span className="ps">$</span>
          <button className="btn btn-primary btn-big" onClick={onBackToLive}>
            Back to live
          </button>
          {s.done + s.failed > 0 ? (
            <button className="btn btn-ghost" onClick={() => practiceQueue.clearFinished()}>
              Clear finished
            </button>
          ) : null}
          <Cursor />
        </div>

        <div className="foot">
          Clips are analyzed one at a time in the background — keep hitting; this list updates
          live.
        </div>
      </TermWindow>
    </div>
  );
}
