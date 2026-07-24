// Requests go through the Vite dev proxy ("/api" -> Flask on :5001) because
// the backend serves no CORS headers.
export const API_BASE = "/api";

// Public base URL for the Cloudflare R2 bucket (pub-*.r2.dev or a custom
// domain). The backend returns object keys, not URLs; without this the app
// still shows all stats but cannot play the stored videos.
export const R2_PUBLIC_BASE_URL = (
  import.meta.env.VITE_R2_PUBLIC_BASE_URL || ""
).replace(/\/+$/, "");

// Reference clip names (per shot type) are fetched dynamically from
// GET /pro-clips, which lists pro_videos/tennis/<shot_type>/*.mp4 on the
// backend — see ShotComparisonFlow.

// The three shot types are pipeline-defined (forehand/backhand route to
// GroundStrokeAnalysis, anything else to ServeAnalysis), not data-driven.
export const SHOT_TYPES = ["forehand", "backhand", "serve"];

export function videoUrlFromKey(key) {
  if (!R2_PUBLIC_BASE_URL || !key) return null;
  return `${R2_PUBLIC_BASE_URL}/${key}`;
}

// The backend saves swing-path heatmaps into frontend/heatmaps/ and returns
// filesystem paths like "frontend/heatmaps/<id>_player.png". The dev server
// serves that folder at /heatmaps/, so map the path to its URL.
export function heatmapUrlFromPath(path) {
  if (!path) return null;
  const name = path.split("/").pop();
  return `/heatmaps/${name}`;
}
