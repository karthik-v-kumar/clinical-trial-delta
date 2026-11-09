import { defineConfig } from "vite";

// Static single-page app. Everything it reads at runtime is /data/*.json, which
// scripts/copy-data.mjs copies from the repo's committed data/ before dev and build.
export default defineConfig({
  build: {
    target: "es2022",
    outDir: "dist",
    assetsDir: "assets",
  },
  server: { port: 5173, strictPort: false },
});
