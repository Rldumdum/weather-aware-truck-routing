import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 1400, // MapLibre alone is about 1 MB and is needed on first paint
  },
  server: {
    host: "127.0.0.1",
    port: 5183,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8010" },
  },
});
