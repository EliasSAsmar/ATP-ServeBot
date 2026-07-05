import { useCallback, useEffect, useRef, useState } from "react";
import { getApi, GlbExpiredError } from "../api";
import { GlbViewer } from "../components/GlbViewer";
import { MetricCard } from "../components/MetricCard";
import { TipList } from "../components/TipCard";
import type { Settings } from "../config";
import type { JobResponse } from "../types/api";

/**
 * Analysis Result — hero #2 (UI.md §5). three.js GLB viewer + elbow metric
 * card + tip cards, all under the "AI 3D estimate — single camera" framing
 * (OVERVIEW.md §5). Handles mesh-URL expiry by transparently re-polling the
 * job for a fresh presigned glb_url.
 */

const MIN_TIP_CONFIDENCE = 0.5; // METRICS.md §9.3

type MeshState = "loading" | "refreshing" | "ready" | "error";

export function ResultScreen({
  settings,
  job,
  onNewServe,
}: {
  settings: Settings;
  job: JobResponse;
  onNewServe: () => void;
}) {
  const [currentJob, setCurrentJob] = useState(job);
  const [meshState, setMeshState] = useState<MeshState>("loading");
  const [glbData, setGlbData] = useState<ArrayBuffer | null>(null);
  const refreshCount = useRef(0);

  const result = currentJob.result;

  const loadMesh = useCallback(async () => {
    if (!result || result.keyframes.length === 0) {
      setMeshState("error");
      return;
    }
    const api = getApi(settings);
    let mesh = result.keyframes[0].mesh;

    const refreshJob = async (): Promise<boolean> => {
      if (refreshCount.current >= 2) return false;
      refreshCount.current++;
      setMeshState("refreshing");
      try {
        const fresh = await api.getServe(currentJob.job_id);
        if (fresh.status === "succeeded" && fresh.result) {
          setCurrentJob(fresh);
          mesh = fresh.result.keyframes[0].mesh;
          return true;
        }
      } catch (e) {
        console.error("[result] failed to refresh job for fresh glb_url", e);
      }
      return false;
    };

    // Proactive refresh when the URL is already expired by server truth.
    if (new Date(mesh.glb_expires_at).getTime() <= Date.now()) {
      if (!(await refreshJob())) {
        setMeshState("error");
        return;
      }
    }

    try {
      const data = await api.fetchGlb(mesh.glb_url);
      setGlbData(data);
      setMeshState("ready");
    } catch (e) {
      if (e instanceof GlbExpiredError && (await refreshJob())) {
        try {
          const data = await api.fetchGlb(mesh.glb_url);
          setGlbData(data);
          setMeshState("ready");
          return;
        } catch (retryErr) {
          console.error("[result] mesh fetch failed after refresh", retryErr);
        }
      } else {
        console.error("[result] mesh fetch failed", e);
      }
      setMeshState("error");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings, currentJob.job_id]);

  useEffect(() => {
    void loadMesh();
  }, [loadMesh]);

  if (!result) {
    return (
      <div className="screen result-screen">
        <div className="card">
          <p className="error-note">No result payload on this job.</p>
          <button className="btn btn-primary" onClick={onNewServe}>
            New serve
          </button>
        </div>
      </div>
    );
  }

  const metric = result.metrics.elbow_angle_deg;
  const uncertain = metric !== null && metric.value !== null && metric.confidence < MIN_TIP_CONFIDENCE;
  const unavailable = metric !== null && metric.value === null;

  return (
    <div className="screen result-screen">
      <div className="viewer-wrap">
        {meshState === "ready" && glbData ? (
          <GlbViewer glbData={glbData} />
        ) : (
          <div className="viewer-placeholder">
            {meshState === "error" ? (
              <>
                <p className="error-note">Couldn&rsquo;t load the 3D model.</p>
                <button
                  className="btn"
                  onClick={() => {
                    refreshCount.current = 0;
                    setMeshState("loading");
                    void loadMesh();
                  }}
                >
                  Retry
                </button>
              </>
            ) : (
              <>
                <div className="spinner" aria-hidden="true" />
                <p className="muted">
                  {meshState === "refreshing" ? "Refreshing 3D model…" : "Loading 3D model…"}
                </p>
              </>
            )}
          </div>
        )}
        {/* Product stance: persistent framing chip on every 3D surface. */}
        <span className="chip chip-framing">AI 3D estimate — single camera</span>
      </div>

      <div className="result-cards">
        {metric !== null ? <MetricCard metric={metric} /> : null}
        {/* Stubbed (null) metrics are hidden in v1 — UI.md §5.2 recommended option. */}

        <TipList tips={result.tips} suppressed={uncertain || unavailable} />
        {uncertain ? (
          <section className="card tip-card tip-neutral">
            <p>
              The AI wasn&rsquo;t confident about your form on this serve — try again with your
              whole body clearly in frame and good lighting.
            </p>
          </section>
        ) : null}

        <p className="muted small framing-note">
          The 3D model and angle are AI estimates inferred from a single camera — use them as
          directional feedback, not as measurements.
        </p>

        <button className="btn btn-primary btn-big" onClick={onNewServe}>
          New serve
        </button>
      </div>
    </div>
  );
}
