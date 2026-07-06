import { useCallback, useEffect, useRef, useState } from "react";
import { getApi, GlbExpiredError } from "../api";
import { GlbViewer } from "../components/GlbViewer";
import { MetricCard } from "../components/MetricCard";
import {
  BallTrajectoryPanel,
  ContactHeightPanel,
  KineticChainPanel,
  PhaseTimelinePanel,
  TossPanel,
} from "../components/Phase2Panels";
import { Cursor, Panel, TermWindow } from "../components/Terminal";
import { TipList } from "../components/TipCard";
import type { Settings } from "../config";
import type { JobResponse, ServeMetrics } from "../types/api";

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

  const serveCtx = `~/serves/${currentJob.job_id.slice(0, 12)}`;

  if (!result) {
    return (
      <div className="stage result-screen">
        <TermWindow context={serveCtx} className="result-win">
          <Panel label="error" className="panel-error" ariaLabel="Result error">
            <p className="error-note">No result payload on this job.</p>
          </Panel>
          <div className="actions">
            <span className="ps">$</span>
            <button className="btn btn-primary" onClick={onNewServe}>
              New serve
            </button>
            <Cursor />
          </div>
        </TermWindow>
      </div>
    );
  }

  const m = result.metrics;
  const metric = m.elbow_angle_deg;
  const uncertain = metric !== null && metric.value !== null && metric.confidence < MIN_TIP_CONFIDENCE;
  const unavailable = metric !== null && metric.value === null;
  const keyframe = result.keyframes.length > 0 ? result.keyframes[0] : null;
  const tracking = result.tracking ?? null;
  const contactMs = m.phase_timing?.contact_ms ?? result.contact.refined_timestamp_ms;
  // Contract §4c: metric key === null → not computed → listed as a stub, never fabricated.
  const pendingKeys = (Object.keys(m) as (keyof ServeMetrics)[]).filter((k) => m[k] === null);

  return (
    <div className="stage result-screen">
      <TermWindow context={serveCtx} className="result-win">
        <div className="cmd">
          <span className="prompt">$</span> servebot analyze <span className="path">serve.webm</span>{" "}
          <span className="flag">--hand {result.handedness}</span>
        </div>
        <div className="log">
          <span>
            <span className="ok">✓</span> contact keyframe&nbsp;
            <span className="val">
              t={(result.contact.refined_timestamp_ms / 1000).toFixed(2)}s · frame{" "}
              {result.contact.refined_frame_index}
            </span>
          </span>
          {keyframe ? (
            <span>
              <span className="ok">✓</span> mesh reconstructed&nbsp;
              <span className="val">{keyframe.keypoints_3d.count} keypoints</span>
            </span>
          ) : null}
        </div>

        <Panel label="reconstruction" meta="contact frame" ariaLabel="3D reconstruction">
          <div className="render">
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
            {meshState === "ready" && glbData ? (
              <span className="drag" aria-hidden="true">
                ⟲ drag to rotate
              </span>
            ) : null}
            {/* Product stance: persistent framing tag on every 3D surface. */}
            <span className="rtag">
              <b>AI 3D estimate</b> — single camera
            </span>
          </div>
        </Panel>

        {metric !== null ? <MetricCard metric={metric} /> : null}

        {/* Phase-2 breakdown — each panel renders only when the backend computed it. */}
        {m.shoulder_angle_deg !== null ? (
          <MetricCard metric={m.shoulder_angle_deg} label="shoulder_angle" subject="arm" />
        ) : null}
        {m.knee_flexion_deg !== null ? (
          <MetricCard
            metric={m.knee_flexion_deg}
            label="knee_flexion"
            subject="leg"
            meta={`${m.knee_flexion_deg.side === "left" ? "left" : "right"} · deepest bend`}
            maxDeg={90}
          />
        ) : null}
        {m.contact_height !== null ? <ContactHeightPanel metric={m.contact_height} /> : null}
        {m.phase_timing !== null ? <PhaseTimelinePanel timing={m.phase_timing} /> : null}
        {m.kinetic_chain_sequence !== null ? (
          <KineticChainPanel chain={m.kinetic_chain_sequence} contactMs={contactMs} />
        ) : null}
        {m.toss_placement !== null ? <TossPanel toss={m.toss_placement} tracking={tracking} /> : null}
        {tracking !== null ? <BallTrajectoryPanel tracking={tracking} /> : null}

        <TipList tips={result.tips} suppressed={uncertain || unavailable} />
        {uncertain ? (
          <Panel label="coach" className="coach" ariaLabel="Coaching">
            <p>
              <span className="arrow">&gt;</span> The AI wasn&rsquo;t confident about your form on
              this serve — try again with your whole body clearly in frame and good lighting.
            </p>
          </Panel>
        ) : null}

        {pendingKeys.length > 0 ? (
          <div className="stubs" aria-label="Metrics not computed for this serve">
            {pendingKeys.map((k) => (
              <span key={k}>
                <b>{k}</b>=—
              </span>
            ))}
            <span className="p2">[coming soon]</span>
          </div>
        ) : null}

        <div className="actions">
          <span className="ps">$</span>
          <button className="btn btn-primary btn-big" onClick={onNewServe}>
            New serve
          </button>
          <Cursor />
        </div>

        <div className="foot">
          The 3D model and every metric are AI estimates inferred from a single camera — use them
          as directional feedback, not as measurements.
        </div>
      </TermWindow>
    </div>
  );
}
