import { useCallback, useEffect, useRef, useState } from "react";
import { ClipRecorder } from "../capture/clipRecorder";
import { CloudStatusChip } from "../components/CloudStatusChip";
import { Panel, TermWindow } from "../components/Terminal";
import type { Settings } from "../config";
import { DETECTOR_VERSION, ServeDetector, type ServeDetection } from "../detect/serveDetector";
import type { CapturedClip } from "../flow/analysis";
import { useHealth } from "../hooks/useHealth";
import { PoseEngine } from "../pose/poseEngine";
import { drawSkeleton } from "../pose/skeletonDraw";
import type { ClipMeta } from "../types/api";

/**
 * Live view — hero #1 (UI.md §3). Full-bleed camera + MediaPipe skeleton
 * overlay (~30fps) + serve auto-detect + always-rolling ring buffer.
 * Works with the cloud entirely absent (ARCHITECTURE.md §2): only the
 * post-serve analysis needs the network.
 */

type LiveState =
  | "initializing"
  | "tracking"
  | "no_pose"
  | "permission_denied"
  | "camera_error";

type CaptureMode = "auto" | "manual";

const NO_POSE_HIDE_FRAMES = 25; // debounce before showing the "step back" hint

export function LiveScreen({
  settings,
  onCaptured,
  onOpenSettings,
}: {
  settings: Settings;
  onCaptured: (clip: CapturedClip) => void;
  onOpenSettings: () => void;
}) {
  const health = useHealth(settings);
  const [liveState, setLiveState] = useState<LiveState>("initializing");
  const [cameraMessage, setCameraMessage] = useState<string>("");
  const [poseUnavailable, setPoseUnavailable] = useState(false);
  const [recorderUnavailable, setRecorderUnavailable] = useState(false);
  const [captureMode, setCaptureMode] = useState<CaptureMode>("auto");
  const [flash, setFlash] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [restartKey, setRestartKey] = useState(0);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const engineRef = useRef<PoseEngine | null>(null);
  const recorderRef = useRef<ClipRecorder | null>(null);
  const detectorRef = useRef(new ServeDetector(settings.handedness));
  const captureModeRef = useRef<CaptureMode>(captureMode);
  const capturingRef = useRef(false);
  const noPoseFrames = useRef(0);
  const rafRef = useRef(0);
  const onCapturedRef = useRef(onCaptured);
  onCapturedRef.current = onCaptured;
  captureModeRef.current = captureMode;

  useEffect(() => {
    detectorRef.current.setHandedness(settings.handedness);
  }, [settings.handedness]);

  const finalizeCapture = useCallback(
    async (contactTimeMs: number, source: "auto" | "manual", detection: ServeDetection | null) => {
      const recorder = recorderRef.current;
      const video = videoRef.current;
      const stream = streamRef.current;
      if (!recorder || !video || !stream || capturingRef.current) return;
      capturingRef.current = true;
      setCapturing(true);
      setFlash(true);
      try {
        const capture = await recorder.capture(contactTimeMs);
        const track = stream.getVideoTracks()[0];
        const trackSettings = track?.getSettings() ?? {};
        const meta: ClipMeta = {
          duration_ms: capture.durationMs,
          fps: Math.round(trackSettings.frameRate ?? 30),
          width: trackSettings.width ?? video.videoWidth,
          height: trackSettings.height ?? video.videoHeight,
          content_type: capture.contentType,
        };
        const clip: CapturedClip = {
          blob: capture.blob,
          meta,
          contactTimestampMs: Math.max(0, Math.min(meta.duration_ms, Math.round(contactTimeMs - capture.startTimeMs))),
          ...(detection
            ? {
                edgeDetect: {
                  detector_version: DETECTOR_VERSION,
                  contact_confidence: detection.confidence,
                  peak_wrist_velocity_px_s: Math.round(detection.peakWristVelocityPxS * 10) / 10,
                  arm_elevation_deg_at_contact: detection.armElevationDeg,
                },
              }
            : {}),
          capturedAt: Date.now(),
          source,
        };
        // Step-1 acceptance: clip blob + metadata logged/inspectable.
        console.info("[serve-capture] clip ready", {
          ...meta,
          byte_size: capture.blob.size,
          contact_timestamp_ms: clip.contactTimestampMs,
          handedness: settings.handedness,
          source,
          edge_detect: clip.edgeDetect ?? null,
        });
        onCapturedRef.current(clip);
      } catch (e) {
        console.error("[serve-capture] failed to finalize clip", e);
      } finally {
        capturingRef.current = false;
        setCapturing(false);
        setFlash(false);
      }
    },
    [settings.handedness],
  );

  // Camera + pose + recorder lifecycle.
  useEffect(() => {
    let cancelled = false;

    async function init() {
      setLiveState("initializing");
      setPoseUnavailable(false);

      if (!navigator.mediaDevices?.getUserMedia) {
        setLiveState("camera_error");
        setCameraMessage("This browser does not support camera access.");
        return;
      }

      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
      } catch (e) {
        if (cancelled) return;
        const err = e as DOMException;
        if (err?.name === "NotAllowedError" || err?.name === "SecurityError") {
          setLiveState("permission_denied");
          setCameraMessage("Camera access was denied. Allow it in your browser settings, then retry.");
        } else {
          setLiveState("camera_error");
          setCameraMessage(`Could not start the camera${err?.message ? `: ${err.message}` : "."}`);
        }
        return;
      }
      if (cancelled) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;
      const video = videoRef.current!;
      video.srcObject = stream;
      await video.play().catch(() => undefined);

      // Ring buffer starts immediately — the buffer is always rolling.
      if (ClipRecorder.isSupported()) {
        const recorder = new ClipRecorder(stream);
        recorder.start();
        recorderRef.current = recorder;
      } else {
        setRecorderUnavailable(true);
      }

      // Pose model — failure degrades to manual capture without an overlay.
      const engine = new PoseEngine();
      try {
        await engine.init();
        if (cancelled) {
          engine.close();
          return;
        }
        engineRef.current = engine;
      } catch (e) {
        console.error("[pose] failed to load pose landmarker", e);
        if (!cancelled) setPoseUnavailable(true);
      }

      if (cancelled) return;
      setLiveState("tracking");
      noPoseFrames.current = 0;

      const canvas = canvasRef.current!;
      const loop = () => {
        rafRef.current = requestAnimationFrame(loop);
        const v = videoRef.current;
        const eng = engineRef.current;
        if (!v || v.readyState < 2) return;
        if (canvas.width !== v.videoWidth || canvas.height !== v.videoHeight) {
          canvas.width = v.videoWidth;
          canvas.height = v.videoHeight;
        }
        if (!eng) return;
        const now = performance.now();
        const frame = eng.detect(v, now);
        if (!frame) return;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (frame.landmarks) {
          noPoseFrames.current = 0;
          setLiveState((s) => (s === "no_pose" || s === "tracking" ? "tracking" : s));
          drawSkeleton(ctx, frame.landmarks, canvas.width, canvas.height, capturingRef.current);
        } else {
          noPoseFrames.current++;
          if (noPoseFrames.current > NO_POSE_HIDE_FRAMES) {
            setLiveState((s) => (s === "tracking" ? "no_pose" : s));
          }
        }
        // Serve auto-detect — armed only in Auto mode and while not finalizing.
        const detection = detectorRef.current.update(frame.landmarks, now, v.videoHeight);
        if (detection && captureModeRef.current === "auto" && !capturingRef.current) {
          void finalizeCapture(detection.contactTimeMs, "auto", detection);
        }
      };
      rafRef.current = requestAnimationFrame(loop);
    }

    void init();

    return () => {
      cancelled = true;
      cancelAnimationFrame(rafRef.current);
      recorderRef.current?.stop();
      recorderRef.current = null;
      engineRef.current?.close();
      engineRef.current = null;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, [restartKey, finalizeCapture]);

  const manualCapture = useCallback(() => {
    if (capturingRef.current || !recorderRef.current) return;
    const now = performance.now();
    const lastPeak = detectorRef.current.lastPeakTimeMs;
    // If the detector saw a reach peak in the last few seconds, treat that as
    // contact; otherwise assume the user tapped right after their serve.
    const contact = lastPeak !== null && now - lastPeak < 4000 ? lastPeak : Math.max(0, now - 400);
    void finalizeCapture(contact, "manual", null);
  }, [finalizeCapture]);

  const blocking = liveState === "permission_denied" || liveState === "camera_error";

  const detectorArmed =
    captureMode === "auto" && !poseUnavailable && !blocking && liveState !== "initializing";

  return (
    <div className="stage live-stage">
      <TermWindow context="~/live" className="live-win">
        <div className="hud">
          <span className="rec" title="Recording buffer is rolling">
            <span
              className={`rec-dot ${recorderUnavailable ? "rec-off" : ""}`}
              aria-hidden="true"
            />
            <span>{recorderUnavailable ? "no recorder" : "REC · buffering"}</span>
          </span>
          <span aria-hidden="true">detector: {detectorArmed ? "armed" : "off"}</span>
          <span className="spacer" />
          <span>hand={settings.handedness}</span>
          <CloudStatusChip status={health.status} mock={settings.mockApi} compact />
          <button className="btn btn-ghost btn-icon" onClick={onOpenSettings} aria-label="Settings">
            &#9881;
          </button>
        </div>

        <div className="live-pane" aria-label="Camera preview with skeleton overlay">
          <video ref={videoRef} className="live-video mirrored" playsInline muted />
          <canvas ref={canvasRef} className="live-canvas mirrored" />

          {liveState === "initializing" ? (
            <div className="live-center-note">
              <div className="spinner" aria-hidden="true" />
              <p>Starting camera / loading pose model…</p>
            </div>
          ) : null}

          {liveState === "no_pose" && !poseUnavailable ? (
            <div className="live-center-note subtle">
              <p>Step back so your whole body is in frame.</p>
            </div>
          ) : null}

          {poseUnavailable && !blocking ? (
            <div className="live-banner">
              Pose tracking unavailable — auto-detect is off, but manual capture still works.
            </div>
          ) : null}

          {flash ? (
            <div className="capture-flash" role="status">
              <span>✓ Captured!</span>
            </div>
          ) : null}

          {blocking ? (
            <div className="live-blocking">
              <Panel label="error" className="panel-error" ariaLabel="Camera error">
                <h3>
                  {liveState === "permission_denied" ? "Camera permission needed" : "Camera error"}
                </h3>
                <p className="error-note">{cameraMessage}</p>
                <button className="btn btn-primary" onClick={() => setRestartKey((k) => k + 1)}>
                  Retry
                </button>
              </Panel>
            </div>
          ) : null}
        </div>

        {!blocking ? (
          <div className="live-controls">
            <div className="mode-toggle" role="radiogroup" aria-label="Capture mode">
              <button
                role="radio"
                aria-checked={captureMode === "auto"}
                className={`btn btn-toggle ${captureMode === "auto" ? "btn-active" : ""}`}
                onClick={() => setCaptureMode("auto")}
                disabled={poseUnavailable}
                title={poseUnavailable ? "Auto-detect needs pose tracking" : undefined}
              >
                Auto
              </button>
              <button
                role="radio"
                aria-checked={captureMode === "manual"}
                className={`btn btn-toggle ${captureMode === "manual" ? "btn-active" : ""}`}
                onClick={() => setCaptureMode("manual")}
              >
                Manual
              </button>
            </div>

            <button
              className="btn btn-primary btn-capture"
              onClick={manualCapture}
              disabled={capturing || recorderUnavailable || liveState === "initializing"}
              aria-label="Capture the last few seconds"
            >
              {capturing ? "Saving…" : "Capture"}
            </button>
          </div>
        ) : null}

        {detectorArmed ? (
          <div className="live-hint">Serve naturally — the app detects it automatically.</div>
        ) : null}
      </TermWindow>
    </div>
  );
}
