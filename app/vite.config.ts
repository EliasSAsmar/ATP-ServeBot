import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // The mock API serves a bundled placeholder mesh; teach Vite that .glb is an asset.
  assetsInclude: ["**/*.glb"],
  server: {
    port: 5173,
  },
  build: {
    rollupOptions: {
      output: {
        // keep the heavy renderers out of the main bundle
        manualChunks: {
          three: ["three"],
          mediapipe: ["@mediapipe/tasks-vision"],
        },
      },
    },
  },
});
