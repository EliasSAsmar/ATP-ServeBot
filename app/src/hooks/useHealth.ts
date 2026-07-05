import { useCallback, useEffect, useRef, useState } from "react";
import { getApi, NetworkUnreachableError } from "../api";
import type { Settings } from "../config";
import type { HealthResponse } from "../types/api";

/**
 * Cloud status per UI.md §2:
 *   ready    — reachable + models_ready
 *   warming  — reachable, models still loading (or 503 models_not_ready)
 *   offline  — network-layer failure IS the down signal (API_CONTRACT.md §1)
 */
export type CloudStatus = "checking" | "ready" | "warming" | "offline";

export interface HealthState {
  status: CloudStatus;
  detail: HealthResponse | null;
  refresh: () => void;
}

export function useHealth(settings: Settings, intervalMs = 12000): HealthState {
  const [status, setStatus] = useState<CloudStatus>("checking");
  const [detail, setDetail] = useState<HealthResponse | null>(null);
  const generation = useRef(0);

  const check = useCallback(async () => {
    const gen = ++generation.current;
    try {
      const health = await getApi(settings).health();
      if (gen !== generation.current) return;
      setDetail(health);
      setStatus(health.instance_up && health.models_ready ? "ready" : "warming");
    } catch (e) {
      if (gen !== generation.current) return;
      setDetail(null);
      // Unreachable or errored (e.g. 503 models_not_ready → warming)
      if (e instanceof NetworkUnreachableError) setStatus("offline");
      else if (e && typeof e === "object" && "code" in e && (e as { code: string }).code === "models_not_ready")
        setStatus("warming");
      else setStatus("offline");
    }
  }, [settings]);

  useEffect(() => {
    setStatus("checking");
    void check();
    const timer = window.setInterval(() => void check(), intervalMs);
    return () => window.clearInterval(timer);
  }, [check, intervalMs]);

  return { status, detail, refresh: check };
}
