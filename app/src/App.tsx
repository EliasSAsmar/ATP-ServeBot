import { useCallback, useState } from "react";
import { loadSettings, saveSettings, type Settings } from "./config";
import type { CapturedClip } from "./flow/analysis";
import { AnalyzingScreen } from "./screens/AnalyzingScreen";
import { LiveScreen } from "./screens/LiveScreen";
import { ResultScreen } from "./screens/ResultScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { SetupScreen } from "./screens/SetupScreen";
import type { JobResponse, Sport } from "./types/api";

/**
 * Screen map (UI.md §1):
 * [Setup] → [Live] ⇄ [Analyzing] → [Result] → back to [Live]
 * [Settings] reachable from Live (and Setup).
 */
type Screen =
  | { name: "setup" }
  | { name: "live" }
  | { name: "analyzing"; clip: CapturedClip }
  | { name: "result"; job: JobResponse }
  | { name: "settings"; from: "setup" | "live" };

const SPORTS: Sport[] = ["tennis", "golf"];

/** Sport switcher — shown on the pre-capture screens; the analyzing/result
 *  screens stay pinned to the sport the clip was captured under. */
function SportTabs({ sport, onChange }: { sport: Sport; onChange: (s: Sport) => void }) {
  return (
    <div className="sport-tabs" role="tablist" aria-label="Sport">
      {SPORTS.map((s) => (
        <button
          key={s}
          role="tab"
          aria-selected={sport === s}
          className={`sport-tab ${sport === s ? "sport-tab-active" : ""}`}
          onClick={() => onChange(s)}
        >
          ./{s}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const [settings, setSettings] = useState<Settings>(() => loadSettings());
  const [screen, setScreen] = useState<Screen>({ name: "setup" });

  const updateSettings = useCallback((patch: Partial<Settings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch };
      saveSettings(next);
      return next;
    });
  }, []);

  const withTabs = (el: JSX.Element) => (
    <>
      <SportTabs sport={settings.sport} onChange={(sport) => updateSettings({ sport })} />
      {el}
    </>
  );

  switch (screen.name) {
    case "setup":
      return withTabs(
        <SetupScreen
          settings={settings}
          onSettingsChange={updateSettings}
          onContinue={() => setScreen({ name: "live" })}
          onOpenSettings={() => setScreen({ name: "settings", from: "setup" })}
        />,
      );
    case "live":
      return withTabs(
        <LiveScreen
          settings={settings}
          onSettingsChange={updateSettings}
          onCaptured={(clip) => setScreen({ name: "analyzing", clip })}
          onOpenSettings={() => setScreen({ name: "settings", from: "live" })}
        />,
      );
    case "analyzing":
      return (
        <AnalyzingScreen
          settings={settings}
          clip={screen.clip}
          onSucceeded={(job) => setScreen({ name: "result", job })}
          onBackToLive={() => setScreen({ name: "live" })}
        />
      );
    case "result":
      return (
        <ResultScreen
          settings={settings}
          job={screen.job}
          onNewServe={() => setScreen({ name: "live" })}
        />
      );
    case "settings":
      return (
        <SettingsScreen
          settings={settings}
          onSettingsChange={updateSettings}
          onBack={() => setScreen(screen.from === "setup" ? { name: "setup" } : { name: "live" })}
        />
      );
  }
}
