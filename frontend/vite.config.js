import { existsSync, createReadStream } from "node:fs";
import { join } from "node:path";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The analysis backend writes swing-path heatmap PNGs into frontend/heatmaps/
// (outside Vite's public dir), so serve that folder at /heatmaps/*.
function serveHeatmaps() {
  return {
    name: "serve-heatmaps",
    configureServer(server) {
      server.middlewares.use("/heatmaps", (req, res, next) => {
        const name = decodeURIComponent(req.url.split("?")[0]).replace(
          /^\/+/,
          ""
        );
        // Only plain filenames — no path traversal.
        if (!/^[\w.-]+\.png$/i.test(name)) return next();
        const file = join(__dirname, "heatmaps", name);
        if (!existsSync(file)) return next();
        res.setHeader("Content-Type", "image/png");
        createReadStream(file).pipe(res);
      });
    },
  };
}

// The Flask backend (default localhost:5001) has no CORS headers, so the dev
// server proxies /api/* to it. Set VITE_BACKEND_URL to point elsewhere.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backend = env.VITE_BACKEND_URL || "http://localhost:5001";
  return {
    plugins: [react(), serveHeatmaps()],
    server: {
      port: 3000,
      proxy: {
        "/api": {
          target: backend,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
          // /process-tennis-video can take minutes; never time the proxy out early.
          timeout: 0,
          proxyTimeout: 0,
        },
      },
    },
  };
});
