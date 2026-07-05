import { useCallback, useEffect, useRef, useState } from "react";
import { CloudStatusChip } from "../components/CloudStatusChip";
import type { Settings } from "../config";
import { useHealth } from "../hooks/useHealth";
import type { Handedness } from "../types/api";

/**
 * Setup / Permissions screen (UI.md §2): camera permission, handedness
 * selector (required, persisted), cloud status chip via GET /v1/health.
 * The live tier has no cloud dependency — "Cloud offline" only disables
 * analysis, not the camera/overlay.
 */

type CameraState = "idle" | "requesting" | "granted" | "denied" | "unavailable";

export function SetupScreen({
  settings,
  onSettingsChange,
  onContinue,
  onOpenSettings,
}: {
  settings: Settings;
  onSettingsChange: (patch: Partial<Settings>) => void;
  onContinue: () => void;
  onOpenSettings: () => void;
}) {
  const health = useHealth(settings);
  const [camera, setCamera] = useState<CameraState>("idle");
  const [cameraError, setCameraError] = useState<string | null>(null);
  const requested = useRef(false);

  const requestCamera = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setCamera("unavailable");
      setCameraError("This browser does not support camera access (getUserMedia).");
      return;
    }
    setCamera("requesting");
    setCameraError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      // Probe only — the Live screen opens its own stream.
      stream.getTracks().forEach((t) => t.stop());
      setCamera("granted");
    } catch (e) {
      const err = e as DOMException;
      if (err?.name === "NotAllowedError" || err?.name === "SecurityError") {
        setCamera("denied");
        setCameraError(
          "Camera access was denied. Allow camera access for this site in your browser settings, then retry.",
        );
      } else if (err?.name === "NotFoundError" || err?.name === "OverconstrainedError") {
        setCamera("unavailable");
        setCameraError("No camera was found on this device.");
      } else {
        setCamera("unavailable");
        setCameraError(`Could not start the camera${err?.message ? `: ${err.message}` : "."}`);
      }
    }
  }, []);

  // Ask once automatically so returning users breeze through.
  useEffect(() => {
    if (!requested.current) {
      requested.current = true;
      void requestCamera();
    }
  }, [requestCamera]);

  return (
    <div className="screen setup-screen">
      <header className="setup-header">
        <h1>ServeBot</h1>
        <p className="muted">
          Live skeleton on your serve, then an AI-estimated 3D look at your contact moment —
          inferred from a single camera, built for directional feedback.
        </p>
      </header>

      <section className="card">
        <h3>1 &middot; Camera</h3>
        {camera === "granted" ? (
          <p className="ok-note">Camera ready.</p>
        ) : camera === "requesting" || camera === "idle" ? (
          <p className="muted">Requesting camera permission…</p>
        ) : (
          <>
            <p className="error-note">{cameraError}</p>
            {camera === "denied" ? (
              <ol className="muted small">
                <li>Open your browser&rsquo;s site settings (lock icon in the address bar).</li>
                <li>Set Camera to &ldquo;Allow&rdquo; for this site.</li>
                <li>Retry below.</li>
              </ol>
            ) : null}
            <button className="btn" onClick={() => void requestCamera()}>
              Retry camera
            </button>
          </>
        )}
      </section>

      <section className="card">
        <h3>2 &middot; Which arm do you serve with?</h3>
        <div className="handedness-row" role="radiogroup" aria-label="Handedness">
          {(["right", "left"] as Handedness[]).map((h) => (
            <button
              key={h}
              role="radio"
              aria-checked={settings.handedness === h}
              className={`btn btn-toggle ${settings.handedness === h ? "btn-active" : ""}`}
              onClick={() => onSettingsChange({ handedness: h })}
            >
              {h === "right" ? "Right-handed" : "Left-handed"}
            </button>
          ))}
        </div>
      </section>

      <section className="card">
        <h3>3 &middot; Cloud analysis</h3>
        <div className="row-between">
          <CloudStatusChip status={health.status} mock={settings.mockApi} />
          <button className="btn btn-ghost" onClick={onOpenSettings}>
            Settings
          </button>
        </div>
        {health.status === "offline" ? (
          <p className="muted small">
            Live tracking and capture still work — only the post-serve 3D analysis needs the cloud.
          </p>
        ) : null}
      </section>

      <button className="btn btn-primary btn-big" onClick={onContinue} disabled={camera !== "granted"}>
        Continue to live view
      </button>
      {camera !== "granted" ? (
        <p className="muted small center">Camera permission is required for the live view.</p>
      ) : null}
    </div>
  );
}
