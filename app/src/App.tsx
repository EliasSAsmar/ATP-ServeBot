import { useCallback, useState } from "react";
import { loadSettings, saveSettings, type Settings } from "./config";
import type { CapturedClip } from "./flow/analysis";
import { AnalyzingScreen } from "./screens/AnalyzingScreen";
import { LiveScreen } from "./screens/LiveScreen";
import { ResultScreen } from "./screens/ResultScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { SetupScreen } from "./screens/SetupScreen";
import type { JobResponse } from "./types/api";

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

  switch (screen.name) {
    case "setup":
      return (
        <SetupScreen
          settings={settings}
          onSettingsChange={updateSettings}
          onContinue={() => setScreen({ name: "live" })}
          onOpenSettings={() => setScreen({ name: "settings", from: "setup" })}
        />
      );
    case "live":
      return (
        <LiveScreen
          settings={settings}
          onSettingsChange={updateSettings}
          onCaptured={(clip) => setScreen({ name: "analyzing", clip })}
          onOpenSettings={() => setScreen({ name: "settings", from: "live" })}
        />
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
