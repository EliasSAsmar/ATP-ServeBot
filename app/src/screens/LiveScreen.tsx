import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { getApi } from "../api";
import { ClipRecorder } from "../capture/clipRecorder";
import { CloudStatusChip } from "../components/CloudStatusChip";
import { Panel, TermWindow } from "../components/Terminal";
import type { Settings } from "../config";
import { GOLF_DETECTOR_VERSION, GolfSwingDetector } from "../detect/golfSwingDetector";
import { DETECTOR_VERSION, ServeDetector, type ServeDetection } from "../detect/serveDetector";
import type { CapturedClip } from "../flow/analysis";
import { practiceQueue, summarize } from "../flow/practiceQueue";
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
  onSettingsChange,
  onCaptured,
  onOpenSettings,
  onOpenSession,
}: {
  settings: Settings;
  onSettingsChange: (patch: Partial<Settings>) => void;
  onCaptured: (clip: CapturedClip) => void;
  onOpenSettings: () => void;
  onOpenSession: () => void;
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
  const [cameras, setCameras] = useState<MediaDeviceInfo[]>([]);
  /** Zoom capability of the active track (e.g. iPhone 0.5× ultrawide); null = unsupported. */
  const [zoomRange, setZoomRange] = useState<{ min: number; max: number; step: number } | null>(
    null,
  );
  /** Practice mode: every capture is queued for background analysis; the
   *  player keeps hitting instead of bouncing through the analyzing screen. */
  const [practice, setPractice] = useState(false);
  const practiceRef = useRef(practice);
  practiceRef.current = practice;
  const queueItems = useSyncExternalStore(practiceQueue.subscribe, practiceQueue.getSnapshot);
  const q = summarize(queueItems);

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
  // Latest settings without retriggering the camera-init effect (zoom /
  // practice toggles must not restart the stream).
  const settingsRef = useRef(settings);
  settingsRef.current = settings;

  // Golf uses its own swing detector (the serve heuristic is overhead-reach
  // specific). Ref so the rAF loop sees sport switches live.
  const golf = settings.sport === "golf";
  const golfRef = useRef(golf);
  golfRef.current = golf;
  const golfDetectorRef = useRef(new GolfSwingDetector());

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
                  detector_version: golfRef.current ? GOLF_DETECTOR_VERSION : DETECTOR_VERSION,
                  contact_confidence: detection.confidence,
                  peak_wrist_velocity_px_s: Math.round(detection.peakWristVelocityPxS * 10) / 10,
                  arm_elevation_deg_at_contact: detection.armElevationDeg,
                },
              }
            : {}),
          capturedAt: Date.now(),
          source,
        };
        const s = settingsRef.current;
        // Step-1 acceptance: clip blob + metadata logged/inspectable.
        console.info("[serve-capture] clip ready", {
          ...meta,
          byte_size: capture.blob.size,
          contact_timestamp_ms: clip.contactTimestampMs,
          handedness: s.handedness,
          source,
          practice: practiceRef.current,
          edge_detect: clip.edgeDetect ?? null,
        });
        if (practiceRef.current) {
          // Practice: queue for background analysis, stay on the live view.
          practiceQueue.setContext(getApi(s), s.handedness, s.sport);
          if (!practiceQueue.enqueue(clip, s.sport)) {
            console.warn("[practice] queue full — capture dropped");
          }
        } else {
          onCapturedRef.current(clip);
        }
      } catch (e) {
        console.error("[serve-capture] failed to finalize clip", e);
      } finally {
        capturingRef.current = false;
        setCapturing(false);
        setFlash(false);
      }
    },
    [],
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

      // Use the chosen camera if set (e.g. an iPhone via Continuity Camera),
      // otherwise the browser default front camera. Ask for a tall ideal
      // (4:3-ish) so cameras that support it deliver full vertical FOV — a
      // 720p request forces 16:9 and throws away vertical pixels on e.g.
      // the 4:3 FaceTime HD camera. Cameras that are 16:9-only (iPhone)
      // just return their native shape.
      const videoConstraints: MediaTrackConstraints = settings.cameraDeviceId
        ? { deviceId: { exact: settings.cameraDeviceId }, width: { ideal: 1920 }, height: { ideal: 1440 } }
        : { facingMode: "user", width: { ideal: 1920 }, height: { ideal: 1440 } };

      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: false });
      } catch (e) {
        if (cancelled) return;
        const err = e as DOMException;
        if (err?.name === "OverconstrainedError" && settings.cameraDeviceId) {
          // Chosen camera vanished (e.g. iPhone disconnected) — fall back to default.
          onSettingsChange({ cameraDeviceId: "" });
          return;
        }
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

      // Device labels are only exposed after permission is granted, so we
      // enumerate here (populates the camera picker, incl. an iPhone).
      try {
        const devs = await navigator.mediaDevices.enumerateDevices();
        if (!cancelled) setCameras(devs.filter((d) => d.kind === "videoinput"));
      } catch {
        // enumeration unsupported — picker just stays at "default"
      }

      // Optical/sensor zoom, when the camera exposes it (an iPhone via
      // Continuity Camera can go below 1× onto the ultrawide lens). Applied
      // on the track, so preview, pose, and the recorded clip all match.
      {
        const track = stream.getVideoTracks()[0];
        const caps = (track?.getCapabilities?.() ?? {}) as {
          zoom?: { min: number; max: number; step?: number };
        };
        if (!cancelled && caps.zoom && typeof caps.zoom.min === "number") {
          setZoomRange({
            min: caps.zoom.min,
            max: caps.zoom.max,
            step: caps.zoom.step || 0.1,
          });
          const desired = settingsRef.current.cameraZoom;
          if (desired && desired >= caps.zoom.min && desired <= caps.zoom.max) {
            track
              .applyConstraints({ advanced: [{ zoom: desired } as MediaTrackConstraintSet] })
              .catch(() => undefined);
          }
        } else if (!cancelled) {
          setZoomRange(null);
        }
      }

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
        // Auto-detect — armed only in Auto mode and while not finalizing.
        // Tennis watches the serve's overhead reach; golf watches the swing's
        // hand-speed spike through impact.
        const activeDetector = golfRef.current ? golfDetectorRef.current : detectorRef.current;
        const detection = activeDetector.update(frame.landmarks, now, v.videoHeight);
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
  }, [restartKey, finalizeCapture, settings.cameraDeviceId, onSettingsChange]);

  // Keep the camera list fresh so a newly-connected iPhone (Continuity Camera)
  // shows up in the picker without a reload.
  useEffect(() => {
    const md = navigator.mediaDevices;
    if (!md?.addEventListener) return;
    const refresh = () =>
      md
        .enumerateDevices()
        .then((ds) => setCameras(ds.filter((d) => d.kind === "videoinput")))
        .catch(() => undefined);
    md.addEventListener("devicechange", refresh);
    return () => md.removeEventListener("devicechange", refresh);
  }, []);

  const manualCapture = useCallback(() => {
    if (capturingRef.current || !recorderRef.current) return;
    const now = performance.now();
    const activeDetector = golfRef.current ? golfDetectorRef.current : detectorRef.current;
    const lastPeak = activeDetector.lastPeakTimeMs;
    // If the detector saw a reach/swing peak in the last few seconds, treat
    // that as contact; otherwise assume the user tapped right after.
    const contact = lastPeak !== null && now - lastPeak < 4000 ? lastPeak : Math.max(0, now - 400);
    void finalizeCapture(contact, "manual", null);
  }, [finalizeCapture]);

  const applyZoom = useCallback(
    (z: number) => {
      streamRef.current
        ?.getVideoTracks()[0]
        ?.applyConstraints({ advanced: [{ zoom: z } as MediaTrackConstraintSet] })
        .catch(() => undefined);
      onSettingsChange({ cameraZoom: z });
    },
    [onSettingsChange],
  );

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
          <label className="cam-select-wrap" title="Camera source (pick your iPhone via Continuity Camera)">
            <span aria-hidden="true">cam=</span>
            <select
              className="cam-select"
              value={settings.cameraDeviceId}
              onChange={(e) => onSettingsChange({ cameraDeviceId: e.target.value })}
              aria-label="Camera source"
            >
              <option value="">default</option>
              {cameras.map((d, i) => (
                <option key={d.deviceId || i} value={d.deviceId}>
                  {d.label || `camera ${i + 1}`}
                </option>
              ))}
            </select>
          </label>
          {zoomRange ? (
            <label
              className="zoom-wrap"
              title="Camera zoom — go below 1× for the iPhone ultrawide lens"
            >
              <span aria-hidden="true">zoom=</span>
              <input
                type="range"
                className="zoom-slider"
                min={zoomRange.min}
                max={zoomRange.max}
                step={zoomRange.step}
                value={settings.cameraZoom || 1}
                onChange={(e) => applyZoom(Number(e.target.value))}
                aria-label="Camera zoom"
              />
              <span className="zoom-val">{(settings.cameraZoom || 1).toFixed(1)}×</span>
            </label>
          ) : null}
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
              <span>✓ {practice ? "Queued!" : "Captured!"}</span>
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

        {(practice || q.total > 0) && !blocking ? (
          <div className="practice-strip" role="status" aria-label="Practice session">
            <span className="strip-label">session</span>
            <span>{q.total} captured</span>
            {q.waiting > 0 ? <span className="muted">· {q.waiting} waiting</span> : null}
            {q.active > 0 ? <span className="run">· analyzing…</span> : null}
            <span className="ok">· {q.done} done</span>
            {q.failed > 0 ? <span className="err">· {q.failed} failed</span> : null}
            <span className="spacer" />
            <button className="btn btn-ghost" onClick={onOpenSession}>
              results →
            </button>
          </div>
        ) : null}

        {!blocking ? (
          <div className="live-controls">
            <div className="controls-left">
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
              role="switch"
              aria-checked={practice}
              className={`btn btn-toggle ${practice ? "btn-active" : ""}`}
              onClick={() => setPractice((p) => !p)}
              title="Queue every capture and keep hitting — analysis runs in the background"
            >
              Practice
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
          <div className="live-hint">
            {golf
              ? "Swing away — it captures automatically at impact."
              : "Serve naturally — the app detects it automatically."}
            {practice ? " Captures queue up in the background — just keep hitting." : ""}
          </div>
        ) : null}
        {golf && !detectorArmed && !blocking ? (
          <div className="live-hint">Take your swing, then hit Capture within a couple seconds.</div>
        ) : null}
      </TermWindow>
    </div>
  );
}
