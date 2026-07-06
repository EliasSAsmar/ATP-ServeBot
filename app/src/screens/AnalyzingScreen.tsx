import { useEffect, useRef, useState } from "react";
import { getApi } from "../api";
import { Cursor, Panel, TermWindow } from "../components/Terminal";
import type { Settings } from "../config";
import { AnalysisRun, type AnalysisPhase, type CapturedClip } from "../flow/analysis";
import { JOB_STAGES, type JobResponse, type JobStage } from "../types/api";

/**
 * Analyzing view (UI.md §4): drives the API_CONTRACT §6 sequence and maps
 * job `stage` → friendly labels. Never blocks the app; cancelable while
 * uploading; failed jobs offer Retry (same object_key) when retriable.
 */

// UI.md §4 stage → label map
const STAGE_LABELS: Record<JobStage, string> = {
  downloading: "Preparing your serve…",
  decoding: "Preparing your serve…",
  segmenting: "Finding you in the video…",
  selecting_keyframe: "Locating the contact moment…",
  reconstructing: "Building your 3D model…",
  filtering: "Analyzing your form…",
  computing_metrics: "Analyzing your form…",
  generating_tips: "Analyzing your form…",
  uploading_mesh: "Almost ready…",
};

// Golf runs the same stages but is a pure body scan — no form analysis.
const GOLF_STAGE_LABELS: Record<JobStage, string> = {
  downloading: "Preparing your swing…",
  decoding: "Preparing your swing…",
  segmenting: "Finding you in the video…",
  selecting_keyframe: "Picking the swing frame…",
  reconstructing: "Building your 3D body…",
  filtering: "Finalizing the scan…",
  computing_metrics: "Finalizing the scan…",
  generating_tips: "Finalizing the scan…",
  uploading_mesh: "Almost ready…",
};

const STILL_WORKING_MS = 30000;

export function AnalyzingScreen({
  settings,
  clip,
  onSucceeded,
  onBackToLive,
}: {
  settings: Settings;
  clip: CapturedClip;
  onSucceeded: (job: JobResponse) => void;
  onBackToLive: () => void;
}) {
  const [phase, setPhase] = useState<AnalysisPhase>({ kind: "uploading", progress: 0 });
  const [stillWorking, setStillWorking] = useState(false);
  const runRef = useRef<AnalysisRun | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const startedRef = useRef(false);
  const lastChange = useRef(Date.now());
  const onSucceededRef = useRef(onSucceeded);
  onSucceededRef.current = onSucceeded;

  useEffect(() => {
    if (startedRef.current) return; // one run per captured clip
    startedRef.current = true;
    const abort = new AbortController();
    abortRef.current = abort;
    const run = new AnalysisRun(
      getApi(settings),
      clip,
      settings.handedness,
      settings.sport,
      (p) => {
        lastChange.current = Date.now();
        setStillWorking(false);
        if (p.kind === "succeeded") onSucceededRef.current(p.job);
        else setPhase(p);
      },
      abort.signal,
    );
    runRef.current = run;
    void run.start();
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Soft "still working" note when nothing changes for a while (UI.md §4).
  useEffect(() => {
    const t = window.setInterval(() => {
      setStillWorking(Date.now() - lastChange.current > STILL_WORKING_MS);
    }, 2000);
    return () => window.clearInterval(t);
  }, []);

  const cancelUpload = () => {
    abortRef.current?.abort();
    onBackToLive();
  };

  const golf = settings.sport === "golf";
  const stageLabels = golf ? GOLF_STAGE_LABELS : STAGE_LABELS;
  const tryAnotherLabel = golf ? "Try another swing" : "Try another serve";

  let body: JSX.Element;
  switch (phase.kind) {
    case "uploading":
      body = (
        <>
          <div className="log" aria-live="polite">
            <span>
              <span className="run">▸</span> uploading clip{" "}
              <span className="val">Uploading… {Math.round(phase.progress * 100)}%</span>
            </span>
          </div>
          <ProgressBar fraction={phase.progress} />
          <div className="actions">
            <span className="ps">$</span>
            <button className="btn btn-ghost" onClick={cancelUpload}>
              Cancel
            </button>
            <Cursor />
          </div>
        </>
      );
      break;
    case "queued":
      body = (
        <div className="log" aria-live="polite">
          <span>
            <span className="ok">✓</span> clip uploaded
          </span>
          <span>
            <span className="run">▸</span> queued{" "}
            <span className="val">Waiting for an open slot…</span> <Cursor />
          </span>
        </div>
      );
      break;
    case "running": {
      const stageIndex = phase.stage ? JOB_STAGES.indexOf(phase.stage) : -1;
      body = (
        <>
          <p className="stage-label" aria-live="polite">
            {phase.stage ? stageLabels[phase.stage] : "Analyzing…"}
          </p>
          <div className="log">
            <span>
              <span className="ok">✓</span> clip uploaded
            </span>
            {JOB_STAGES.map((s, i) => {
              if (stageIndex < 0 || i > stageIndex) return null;
              return i < stageIndex ? (
                <span key={s}>
                  <span className="ok">✓</span> {s}
                </span>
              ) : (
                <span key={s}>
                  <span className="run">▸</span> {s} <Cursor />
                </span>
              );
            })}
            {stageIndex < 0 ? (
              <span>
                <span className="run">▸</span> analyzing <Cursor />
              </span>
            ) : null}
          </div>
          <ProgressBar fraction={phase.progress} />
        </>
      );
      break;
    }
    case "failed":
      body = (
        <Panel label="error" className="panel-error" ariaLabel="Analysis failed">
          <h2>{golf ? "Couldn’t scan this swing" : "Couldn’t analyze this serve"}</h2>
          <p className="error-note">{phase.message}</p>
          {/* UI.md §8: retriable failures (including a failed upload — the clip
              stays in memory) offer Retry; AnalysisRun.retry() re-uploads if
              needed or re-POSTs /v1/serves with the same object_key. */}
          {phase.retriable ? (
            <div className="actions">
              <span className="ps">$</span>
              <button
                className="btn btn-primary"
                onClick={() => {
                  setPhase(
                    phase.canRetrySameClip ? { kind: "queued" } : { kind: "uploading", progress: 0 },
                  );
                  void runRef.current?.retry();
                }}
              >
                Retry
              </button>
              <button className="btn btn-ghost" onClick={onBackToLive}>
                {tryAnotherLabel}
              </button>
              <Cursor />
            </div>
          ) : (
            <>
              <p className="muted small">
                Make sure your whole body is in frame with good lighting, then try again.
              </p>
              <div className="actions">
                <span className="ps">$</span>
                <button className="btn btn-ghost" onClick={onBackToLive}>
                  {tryAnotherLabel}
                </button>
                <Cursor />
              </div>
            </>
          )}
        </Panel>
      );
      break;
    case "cancelled":
    case "succeeded": // handled by parent before reaching here
      body = <div className="spinner" aria-hidden="true" />;
      break;
  }

  return (
    <div className="stage analyzing-stage">
      <TermWindow context="~/analyze">
        <div className="cmd">
          <span className="prompt">$</span> servebot {golf ? "scan" : "analyze"}{" "}
          <span className="path">{golf ? "swing.webm" : "serve.webm"}</span>{" "}
          {golf ? <span className="flag">--sport golf </span> : null}
          <span className="flag">--hand {settings.handedness}</span>
        </div>
        {body}
        {stillWorking && phase.kind !== "failed" ? (
          <div className="foot">Still working — big models take a moment…</div>
        ) : null}
      </TermWindow>
    </div>
  );
}

/** ASCII block progress bar (terminal style); keeps the progressbar role. */
function ProgressBar({ fraction }: { fraction: number }) {
  const clamped = Math.min(1, Math.max(0, fraction));
  const total = 28;
  const filled = Math.round(clamped * total);
  return (
    <div
      className="progress"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped * 100)}
    >
      <span className="fill">{"█".repeat(filled)}</span>
      <span className="empty">{"░".repeat(total - filled)}</span>
      <span className="pct">{Math.round(clamped * 100)}%</span>
    </div>
  );
}
