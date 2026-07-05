import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// Note: StrictMode is intentionally omitted — its dev-mode double-invoked
// effects would double-start camera streams, MediaRecorders and analysis runs.

createRoot(document.getElementById("root")!).render(<App />);
