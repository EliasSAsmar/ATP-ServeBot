import type { Handedness, Sport } from "./types/api";

/**
 * App settings: env-var defaults, persisted user overrides in localStorage.
 *
 * Env vars (set at build/dev time, see README):
 *   VITE_API_BASE_URL  — backend base URL (default http://localhost:8000)
 *   VITE_API_KEY       — X-API-Key value for /v1 calls
 *   VITE_MOCK_API      — "true"/"false": start in mock-API mode (default true,
 *                        so the app is demonstrable with no backend at all)
 */

export const APP_VERSION = "0.1.0";

export interface Settings {
  /** Active sport tab: tennis (serve analysis) or golf (3D body scan). */
  sport: Sport;
  handedness: Handedness;
  apiBaseUrl: string;
  apiKey: string;
  mockApi: boolean;
  /** Selected camera deviceId ("" = browser default). Enables picking an
   *  external camera such as an iPhone via Continuity Camera. */
  cameraDeviceId: string;
  /** Camera zoom factor (0 = camera default). Only applied when the active
   *  track exposes a zoom capability — e.g. an iPhone's 0.5× ultrawide. */
  cameraZoom: number;
}

const STORAGE_KEY = "servebot.settings.v1";

export const ENV_DEFAULTS: Settings = {
  sport: "tennis",
  handedness: "right",
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000",
  apiKey: import.meta.env.VITE_API_KEY ?? "",
  mockApi:
    import.meta.env.VITE_MOCK_API !== undefined
      ? import.meta.env.VITE_MOCK_API === "true"
      : true,
  cameraDeviceId: "",
  cameraZoom: 0,
};

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...ENV_DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return {
      sport: parsed.sport === "golf" ? "golf" : "tennis",
      handedness: parsed.handedness === "left" ? "left" : "right",
      apiBaseUrl: typeof parsed.apiBaseUrl === "string" && parsed.apiBaseUrl ? parsed.apiBaseUrl : ENV_DEFAULTS.apiBaseUrl,
      apiKey: typeof parsed.apiKey === "string" ? parsed.apiKey : ENV_DEFAULTS.apiKey,
      mockApi: typeof parsed.mockApi === "boolean" ? parsed.mockApi : ENV_DEFAULTS.mockApi,
      cameraDeviceId: typeof parsed.cameraDeviceId === "string" ? parsed.cameraDeviceId : "",
      cameraZoom:
        typeof parsed.cameraZoom === "number" && Number.isFinite(parsed.cameraZoom)
          ? parsed.cameraZoom
          : 0,
    };
  } catch {
    return { ...ENV_DEFAULTS };
  }
}

export function saveSettings(s: Settings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    // localStorage unavailable (private mode etc.) — settings just won't persist
  }
}
