import { CloudStatusChip } from "../components/CloudStatusChip";
import { Panel, TermWindow } from "../components/Terminal";
import type { Settings } from "../config";
import { useHealth } from "../hooks/useHealth";
import type { Handedness } from "../types/api";

/**
 * Settings (UI.md §7): handedness, API endpoint + key (stored locally — no
 * accounts in v1), mock-API toggle, and a live instance-status readout.
 */
export function SettingsScreen({
  settings,
  onSettingsChange,
  onBack,
}: {
  settings: Settings;
  onSettingsChange: (patch: Partial<Settings>) => void;
  onBack: () => void;
}) {
  const health = useHealth(settings);

  return (
    <div className="stage settings-screen">
      <TermWindow context="~/config">
        <div className="row-between">
          <div className="cmd">
            <span className="prompt">$</span> servebot config
          </div>
          <button className="btn btn-ghost" onClick={onBack}>
            Back
          </button>
        </div>

        <Panel label="handedness" ariaLabel="Handedness">
          <p className="muted small" style={{ marginTop: 0 }}>
            Sent with every analysis job — picks which arm the metrics use.
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

        <Panel label="backend" ariaLabel="Backend">
          <label className="field-row">
            <input
              type="checkbox"
              checked={settings.mockApi}
              onChange={(e) => onSettingsChange({ mockApi: e.target.checked })}
            />
            <span>
              Mock API mode
              <span className="muted small block">
                Simulates the whole cloud pipeline in the browser — no backend needed.
              </span>
            </span>
          </label>

          <label className="field">
            <span className="field-label">api_endpoint</span>
            <input
              type="url"
              value={settings.apiBaseUrl}
              disabled={settings.mockApi}
              placeholder="http://localhost:8000"
              onChange={(e) => onSettingsChange({ apiBaseUrl: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">api_key (X-API-Key)</span>
            <input
              type="password"
              value={settings.apiKey}
              disabled={settings.mockApi}
              placeholder="paste your key"
              autoComplete="off"
              onChange={(e) => onSettingsChange({ apiKey: e.target.value })}
            />
          </label>
          <p className="muted small">Stored locally in this browser only — there are no accounts.</p>
        </Panel>

        <Panel label="instance_status" meta="GET /v1/health" ariaLabel="Instance status">
          <div className="row-between">
            <CloudStatusChip status={health.status} mock={settings.mockApi} />
            <button className="btn" onClick={health.refresh}>
              Refresh
            </button>
          </div>
          {health.detail?.gpu ? (
            <p className="muted small">
              gpu={health.detail.gpu.name} · vram={health.detail.gpu.vram_used_mb}/
              {health.detail.gpu.vram_total_mb}MB · queue_depth={health.detail.queue_depth ?? 0}
            </p>
          ) : null}
          <p className="muted small">
            Post-serve analysis needs the instance running. Live tracking and capture always work,
            even fully offline.
          </p>
        </Panel>

        <div className="foot">
          Settings are stored locally in this browser — no accounts in v1.
        </div>
      </TermWindow>
    </div>
  );
}
