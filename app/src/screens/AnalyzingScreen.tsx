import { useEffect, useRef, useState } from "react";
import { getApi } from "../api";
import type { Settings } from "../config";
import { AnalysisRun, type AnalysisPhase, type CapturedClip } from "../flow/analysis";
import type { JobResponse, JobStage } from "../types/api";

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

  let body: JSX.Element;
  switch (phase.kind) {
    case "uploading":
      body = (
        <>
          <h2>Uploading… {Math.round(phase.progress * 100)}%</h2>
          <ProgressBar fraction={phase.progress} />
          <button className="btn btn-ghost" onClick={cancelUpload}>
            Cancel
          </button>
        </>
      );
      break;
    case "queued":
      body = (
        <>
          <h2>Waiting for an open slot…</h2>
          <div className="spinner" aria-hidden="true" />
        </>
      );
      break;
    case "running":
      body = (
        <>
          <h2>{phase.stage ? STAGE_LABELS[phase.stage] : "Analyzing…"}</h2>
          <ProgressBar fraction={phase.progress} />
          <p className="muted small">{phase.stage ? phase.stage.replace(/_/g, " ") : ""}</p>
        </>
      );
      break;
    case "failed":
      body = (
        <div className="card">
          <h2>Couldn&rsquo;t analyze this serve</h2>
          <p className="error-note">{phase.message}</p>
          {/* UI.md §8: retriable failures (including a failed upload — the clip
              stays in memory) offer Retry; AnalysisRun.retry() re-uploads if
              needed or re-POSTs /v1/serves with the same object_key. */}
          {phase.retriable ? (
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
          ) : (
            <p className="muted small">
              Make sure your whole body is in frame with good lighting, then try another serve.
            </p>
          )}
          <button className="btn btn-ghost" onClick={onBackToLive}>
            Try another serve
          </button>
        </div>
      );
      break;
    case "cancelled":
    case "succeeded": // handled by parent before reaching here
      body = <div className="spinner" aria-hidden="true" />;
      break;
  }

  return (
    <div className="screen analyzing-screen">
      <div className="analyzing-body">
        {body}
        {stillWorking && phase.kind !== "failed" ? (
          <p className="muted small">Still working — big models take a moment…</p>
        ) : null}
      </div>
    </div>
  );
}

function ProgressBar({ fraction }: { fraction: number }) {
  return (
    <div
      className="progress"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(fraction * 100)}
    >
      <div className="progress-fill" style={{ width: `${Math.min(100, Math.max(0, fraction * 100))}%` }} />
    </div>
  );
}
