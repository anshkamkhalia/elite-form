import { API_BASE } from "./config";

// Both endpoints are synchronous and can run for minutes on longer clips.
// fetch() has no built-in timeout, so requests simply wait; the caller gets
// an AbortController signal for user-initiated cancellation.
async function postForm(path, formData, signal) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      body: formData,
      signal,
      credentials: "same-origin",
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new Error(
      "Could not reach the backend. Make sure the Flask server is running on port 5001 (bash api/run_api.sh from the repo root)."
    );
  }

  // Unhandled backend exceptions surface as HTML 500 pages, so any non-JSON
  // or non-200 response is treated as "analysis failed".
  const text = await res.text();
  let data = null;
  try {
    data = JSON.parse(text);
  } catch {
    data = null;
  }

  if (!res.ok || data === null) {
    const detail = data?.error;
    throw new Error(
      detail ||
        "Analysis failed on the server. This usually means the clip was too short, the player/net wasn't detected, or the pose couldn't be tracked — try a clearer or longer clip."
    );
  }
  return data;
}

export function processTennisVideo(file, signal) {
  const fd = new FormData();
  fd.append("video", file);
  return postForm("/process-tennis-video", fd, signal);
}

export async function fetchProClips(signal) {
  return jsonRequest("/pro-clips", { signal });
}

export function processShotAnalysis(file, shotType, comparisonPro, signal) {
  const fd = new FormData();
  fd.append("video", file);
  fd.append("shot_type", shotType);
  fd.append("comparison_pro", comparisonPro);
  return postForm("/process-tennis-shot-analysis", fd, signal);
}

// AI coaching feedback generated server-side from the comparison metrics
// (the AI API key never reaches the browser).
export async function fetchCoachingTips(results, shotType, comparisonPro, signal) {
  const res = await fetch(`${API_BASE}/coaching-tips`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      results,
      shot_type: shotType,
      comparison_pro: comparisonPro,
    }),
    signal,
    credentials: "same-origin",
  });
  const data = await res.json().catch(() => null);
  if (!res.ok || !data?.tips) {
    throw new Error(data?.error || "Coaching feedback is unavailable right now.");
  }
  return { tldr: data.tldr || "", tips: data.tips };
}

// ---- auth & history ----

async function jsonRequest(path, { method = "GET", body, signal } = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal,
      credentials: "same-origin",
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new Error(
      "Could not reach the backend. Make sure the Flask server is running on port 5001."
    );
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = new Error(data?.error || "Request failed.");
    err.status = res.status;
    throw err;
  }
  return data;
}

export async function register(username, password) {
  const data = await jsonRequest("/auth/register", {
    method: "POST",
    body: { username, password },
  });
  return data.user;
}

export async function login(username, password) {
  const data = await jsonRequest("/auth/login", {
    method: "POST",
    body: { username, password },
  });
  return data.user;
}

export async function logout() {
  await jsonRequest("/auth/logout", { method: "POST" });
}

export async function fetchMe() {
  const data = await jsonRequest("/auth/me");
  return data.user;
}

export async function fetchHistory() {
  const data = await jsonRequest("/history");
  return data.items;
}

export async function fetchHistoryItem(id, signal) {
  return jsonRequest(`/history/${id}`, { signal });
}

export async function deleteHistoryItem(id) {
  return jsonRequest(`/history/${id}`, { method: "DELETE" });
}

export async function fetchHistorySearchModes() {
  return jsonRequest("/history/search-modes");
}

/** Search saved analyses. mode: all | filename | pro | shot_type | kind */
export async function searchHistory(
  { q = "", mode = "all", kind, shotType, pro } = {},
  signal
) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (mode) params.set("mode", mode);
  if (kind) params.set("kind", kind);
  if (shotType) params.set("shot_type", shotType);
  if (pro) params.set("pro", pro);
  const qs = params.toString();
  return jsonRequest(`/history/search${qs ? `?${qs}` : ""}`, { signal });
}
