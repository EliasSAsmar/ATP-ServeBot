import { useCallback, useEffect, useRef, useState } from "react";
import { CloudStatusChip } from "../components/CloudStatusChip";
import { Cursor, Panel, TermWindow } from "../components/Terminal";
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
    <div className="stage setup-screen">
      <TermWindow context="~/init">
        <div>
          <div className="cmd">
            <span className="prompt">$</span> servebot init
          </div>
          <p className="intro small">
            Live skeleton on your serve, then an AI-estimated 3D look at your contact moment —
            inferred from a single camera, built for directional feedback.
          </p>
        </div>

        <div className="log" role="status" aria-label="Camera check">
          {camera === "granted" ? (
            <span>
              <span className="ok">✓</span> camera <span className="val">Camera ready.</span>
            </span>
          ) : camera === "requesting" || camera === "idle" ? (
            <span>
              <span className="run">▸</span> camera{" "}
              <span className="val">Requesting camera permission…</span> <Cursor />
            </span>
          ) : (
            <span>
              <span className="err">✗</span> camera{" "}
              <span className="err">{camera === "denied" ? "permission_denied" : "unavailable"}</span>
            </span>
          )}
        </div>

        {camera === "denied" || camera === "unavailable" ? (
          <Panel label="camera" className="panel-error" ariaLabel="Camera error">
            <p className="error-note">{cameraError}</p>
            {camera === "denied" ? (
              <ol className="muted small">
                <li>Open your browser&rsquo;s site settings (lock icon in the address bar).</li>
                <li>Set Camera to &ldquo;Allow&rdquo; for this site.</li>
                <li>Retry below.</li>
              </ol>
            ) : null}
            <div>
              <button className="btn" onClick={() => void requestCamera()}>
                Retry camera
              </button>
            </div>
          </Panel>
        ) : null}

        <Panel label="handedness" meta="required">
          <p className="muted small" style={{ marginTop: 0 }}>
            Which arm do you serve with?
          </p>
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
        </Panel>

        <Panel label="cloud" meta="GET /v1/health" ariaLabel="Cloud analysis">
          <div className="row-between">
            <CloudStatusChip status={health.status} mock={settings.mockApi} />
            <button className="btn btn-ghost" onClick={onOpenSettings}>
              Settings
            </button>
          </div>
          {health.status === "offline" ? (
            <p className="muted small">
              Live tracking and capture still work — only the post-serve 3D analysis needs the
              cloud.
            </p>
          ) : null}
        </Panel>

        <div className="actions">
          <span className="ps">$</span>
          <button
            className="btn btn-primary btn-big"
            onClick={onContinue}
            disabled={camera !== "granted"}
          >
            Continue to live view
          </button>
          <Cursor />
        </div>
        {camera !== "granted" ? (
          <div className="foot">Camera permission is required for the live view.</div>
        ) : null}
      </TermWindow>
    </div>
  );
}
