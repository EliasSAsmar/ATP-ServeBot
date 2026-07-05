import type { CloudStatus } from "../hooks/useHealth";

const LABELS: Record<CloudStatus, string> = {
  checking: "Checking cloud…",
  ready: "Cloud ready",
  warming: "Warming up…",
  offline: "Cloud offline — start the instance",
};

const SHORT_LABELS: Record<CloudStatus, string> = {
  checking: "Checking…",
  ready: "Cloud ready",
  warming: "Warming up…",
  offline: "Cloud offline",
};

export function CloudStatusChip({
  status,
  mock,
  compact = false,
}: {
  status: CloudStatus;
  mock?: boolean;
  compact?: boolean;
}) {
  const label = compact ? SHORT_LABELS[status] : LABELS[status];
  return (
    <span className={`chip chip-cloud chip-${status}`} role="status">
      <span className="chip-dot" aria-hidden="true" />
      {label}
      {mock && status === "ready" ? <span className="chip-mock-tag">mock</span> : null}
    </span>
  );
}
