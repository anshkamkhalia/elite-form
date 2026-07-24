// Minimal stand-in for the Flask backend, matching the README's API contract
// plus the newer additions: heatmap PNGs written to frontend/heatmaps/, a
// playable video_url, and the /coaching-tips endpoint (which calls the real
// Gemini API using GEMINI_API_KEY from the repo-root .env, exactly like the
// Flask backend does). Run: npm run mock
import http from "node:http";
import crypto from "node:crypto";
import zlib from "node:zlib";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.join(__dirname, "..");
const HEATMAP_DIR = path.join(__dirname, "heatmaps");
// H.264 sample standing in for presigned R2 URLs (the repo's pro clips are
// MPEG-4 Part 2, which browsers can't decode).
const SAMPLE_VIDEO = path.join(__dirname, "sample-video.mp4");

const PORT = 5001;
const PRO_VIDEOS_ROOT = path.join(REPO_ROOT, "pro_videos", "tennis");

// Mirrors api/app.py's list_pro_clips(): scans pro_videos/tennis/<shot_type>/*.mp4.
function listProClips() {
  const clips = {};
  for (const shotType of ["forehand", "backhand", "serve"]) {
    const dir = path.join(PRO_VIDEOS_ROOT, shotType);
    let names = [];
    try {
      names = fs
        .readdirSync(dir)
        .filter((f) => f.toLowerCase().endsWith(".mp4"))
        .map((f) => f.slice(0, -4));
    } catch {
      /* directory missing */
    }
    clips[shotType] = names.sort();
  }
  return clips;
}

// ---- read GEMINI_API_KEY from repo-root .env (server-side only) ----
function readEnvKey(name) {
  try {
    const text = fs.readFileSync(path.join(REPO_ROOT, ".env"), "utf8");
    for (const line of text.split(/\r?\n/)) {
      const m = /^\s*([A-Z0-9_]+)\s*=\s*(.+)\s*$/.exec(line);
      if (m && m[1] === name) return m[2];
    }
  } catch {
    /* no .env */
  }
  return process.env[name];
}
const GEMINI_API_KEY = readEnvKey("GEMINI_API_KEY");

// ---- mock accounts + saved history (file-backed so it survives restarts) ----
const DATA_FILE = path.join(__dirname, ".mock-data.json");

function loadData() {
  try {
    return JSON.parse(fs.readFileSync(DATA_FILE, "utf8"));
  } catch {
    return { users: [], analyses: [], nextUserId: 1, nextAnalysisId: 1 };
  }
}
function saveData() {
  try {
    fs.writeFileSync(DATA_FILE, JSON.stringify(store, null, 2));
  } catch {
    /* best effort */
  }
}
const store = loadData();

// token -> userId (in-memory sessions, like a signed cookie)
const sessions = new Map();

const USERNAME_RE = /^[A-Za-z0-9_]{3,32}$/;
const MIN_PASSWORD_LEN = 8;

function hashPassword(password, salt = crypto.randomBytes(16).toString("hex")) {
  const hash = crypto.scryptSync(password, salt, 64).toString("hex");
  return `${salt}:${hash}`;
}
function verifyPassword(password, stored) {
  const [salt, hash] = (stored || "").split(":");
  if (!salt || !hash) return false;
  const test = crypto.scryptSync(password, salt, 64).toString("hex");
  const a = Buffer.from(hash, "hex");
  const b = Buffer.from(test, "hex");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function parseCookies(req) {
  const out = {};
  const raw = req.headers.cookie;
  if (!raw) return out;
  for (const part of raw.split(";")) {
    const i = part.indexOf("=");
    if (i === -1) continue;
    out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}
function currentUserId(req) {
  const token = parseCookies(req)["atlas_session"];
  if (!token) return null;
  return sessions.get(token) ?? null;
}
function publicUser(u) {
  return { id: u.id, username: u.username, created_at: u.created_at };
}
function sessionCookie(token) {
  return `atlas_session=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800`;
}
function clearCookie() {
  return "atlas_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0";
}

// ---- tiny PNG writer (grayscale swing-path look-alike, zero deps) ----
function crc32(buf) {
  let c,
    table = crc32.table;
  if (!table) {
    table = crc32.table = new Int32Array(256);
    for (let n = 0; n < 256; n++) {
      c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c;
    }
  }
  c = -1;
  for (let i = 0; i < buf.length; i++) c = (c >>> 8) ^ table[(c ^ buf[i]) & 0xff];
  return (c ^ -1) >>> 0;
}

function pngChunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

// Draws a plausible arc of filled circles on a 640x360 black canvas.
function makeSwingPathPng(seed) {
  const W = 640,
    H = 360;
  const px = new Uint8Array(W * H);
  const rand = (i) => {
    const x = Math.sin(seed * 999 + i * 77) * 10000;
    return x - Math.floor(x);
  };
  for (let t = 0; t < 40; t++) {
    const fx = 120 + t * 10 + rand(t) * 8;
    const fy = 300 - Math.sin((t / 40) * Math.PI) * 220 + rand(t + 50) * 10;
    for (let dy = -6; dy <= 6; dy++)
      for (let dx = -6; dx <= 6; dx++) {
        if (dx * dx + dy * dy > 36) continue;
        const x = Math.round(fx + dx),
          y = Math.round(fy + dy);
        if (x >= 0 && x < W && y >= 0 && y < H) px[y * W + x] = 255;
      }
  }
  // filter byte 0 per scanline
  const raw = Buffer.alloc((W + 1) * H);
  for (let y = 0; y < H; y++) {
    raw[y * (W + 1)] = 0;
    Buffer.from(px.subarray(y * W, (y + 1) * W)).copy(raw, y * (W + 1) + 1);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(W, 0);
  ihdr.writeUInt32BE(H, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 0; // grayscale
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", zlib.deflateSync(raw)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

function writeHeatmaps() {
  fs.mkdirSync(HEATMAP_DIR, { recursive: true });
  const id = crypto.randomUUID();
  const playerPath = `frontend/heatmaps/${id}_player.png`;
  const proPath = `frontend/heatmaps/${id}_pro.png`;
  fs.writeFileSync(path.join(HEATMAP_DIR, `${id}_player.png`), makeSwingPathPng(1 + Math.random()));
  fs.writeFileSync(path.join(HEATMAP_DIR, `${id}_pro.png`), makeSwingPathPng(2 + Math.random()));
  return { playerPath, proPath };
}

// ---- canned analysis results (shapes match the real backend) ----
const serveResults = () => {
  const { playerPath, proPath } = writeHeatmaps();
  return {
    results: {
      shot_type: "serve",
      velocity: {
        average_difference: 3.11,
        peak_difference: 15.34,
        player: { average: 24.05, peak: 59.34 },
        pro: { average: 20.94, peak: 44.0 },
      },
      joint_angles: {
        left_elbow: { player_average: 129.35, pro_average: 137.05, difference: -7.7, player_range: [51.88, 175.97], pro_range: [37.62, 177.79] },
        left_shoulder: { player_average: 84.1, pro_average: 92.6, difference: -8.5, player_range: [20.4, 168.2], pro_range: [15.9, 174.5] },
        left_knee: { player_average: 158.2, pro_average: 151.7, difference: 6.5, player_range: [98.1, 179.4], pro_range: [88.7, 178.9] },
        right_elbow: { player_average: 122.9, pro_average: 118.4, difference: 4.5, player_range: [44.2, 176.8], pro_range: [39.5, 177.1] },
        right_shoulder: { player_average: 96.3, pro_average: 103.8, difference: -7.5, player_range: [25.7, 171.3], pro_range: [22.1, 175.8] },
        right_knee: { player_average: 149.6, pro_average: 144.2, difference: 5.4, player_range: [92.4, 178.6], pro_range: [85.3, 179.2] },
      },
      toss: {
        drift_difference_ft: 0.8,
        player: { toss_drift_ft: 2.1 },
        pro: { toss_drift_ft: 1.3 },
      },
      visuals: {
        player_swing_path: playerPath,
        pro_swing_path: proPath,
        player_frame_w: 1080,
        pro_frame_w: 1920,
      },
    },
    key: `process_tennis_shot_analysis/${crypto.randomUUID()}.mp4`,
    video_url: `http://localhost:${PORT}/sample-video.mp4`,
  };
};

const groundstrokeResults = (shotType) => {
  const r = serveResults();
  r.results.shot_type = shotType;
  delete r.results.toss;
  delete r.results.visuals.player_frame_w;
  delete r.results.visuals.pro_frame_w;
  return r;
};

// ---- coaching via the real Gemini API (mirrors api/coaching.py) ----
const COACH_SYSTEM_PROMPT = `You are an experienced tennis coach reviewing a player's stroke against a professional's reference clip. You are given biomechanical measurements: wrist velocity (mph), average joint angles in degrees for six joints (differences are player minus pro), and for serves, toss drift in feet.

Respond with ONLY a JSON object (no markdown fences) in this exact shape:
{"tldr": "...", "tips": "..."}

- "tldr": one short sentence (max ~20 words) that a busy player can skim — the single most important takeaway.
- "tips": the full coaching notes as plain text. Conversational, encouraging, and specific — sound like a real coach talking to their player, not a report. Lead with the one or two differences that matter most (largest deviations). For each, explain what it means for their shot and give one concrete cue or drill. Mention a strength too. Do not recite raw numbers except where a number genuinely helps. 3 short paragraphs maximum, separated by blank lines. No headings, no bullet lists, no markdown inside the tips string.`;

function summarizeMetrics(results, shotType, pro) {
  const lines = [`Shot type: ${shotType}. Compared against: ${pro}.`];
  const v = results.velocity;
  if (v) {
    lines.push(
      `Wrist velocity (mph): player avg ${v.player?.average}, pro avg ${v.pro?.average}, avg diff ${v.average_difference}; player peak ${v.player?.peak}, pro peak ${v.pro?.peak}, peak diff ${v.peak_difference}.`
    );
  }
  for (const [joint, a] of Object.entries(results.joint_angles ?? {})) {
    lines.push(
      `${joint.replace(/_/g, " ")}: player ${a.player_average}°, pro ${a.pro_average}°, diff ${a.difference}°.`
    );
  }
  const t = results.toss;
  if (t) {
    lines.push(
      `Serve toss drift (ft): player ${t.player?.toss_drift_ft}, pro ${t.pro?.toss_drift_ft}, diff ${t.drift_difference_ft}.`
    );
  }
  return lines.join("\n");
}

function parseCoachingResponse(raw) {
  let text = raw.trim();
  if (text.startsWith("```")) {
    text = text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  }
  try {
    const parsed = JSON.parse(text);
    const tldr = (parsed.tldr || "").trim();
    const tips = (parsed.tips || "").trim();
    if (tips) return { tldr, tips };
  } catch {
    /* fall through */
  }
  return { tldr: "", tips: raw.trim() };
}

async function generateCoachingTips(results, shotType, pro) {
  if (!GEMINI_API_KEY) throw new Error("GEMINI_API_KEY is not configured");
  const res = await fetch(
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
      },
      body: JSON.stringify({
        system_instruction: { parts: [{ text: COACH_SYSTEM_PROMPT }] },
        contents: [
          { role: "user", parts: [{ text: summarizeMetrics(results, shotType, pro) }] },
        ],
        generationConfig: {
          temperature: 0.7,
          maxOutputTokens: 600,
          responseMimeType: "application/json",
        },
      }),
    }
  );
  const data = await res.json();
  if (res.status === 429) {
    throw new Error(
      "The AI service is temporarily rate-limited — wait a minute and retry."
    );
  }
  if (!res.ok) {
    throw new Error(data?.error?.message || `Gemini API error ${res.status}`);
  }
  const text = data?.candidates?.[0]?.content?.parts?.[0]?.text;
  if (!text) throw new Error("Gemini returned no text");
  return parseCoachingResponse(text);
}

// ---- request plumbing ----
function drainBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
  });
}

function parseFormFields(body, contentType) {
  const m = /boundary=(.+)$/.exec(contentType || "");
  if (!m) return { fields: {}, hasVideo: false };
  const boundary = `--${m[1]}`;
  const text = body.toString("latin1");
  const parts = text.split(boundary).slice(1, -1);
  const fields = {};
  let hasVideo = false;
  for (const part of parts) {
    const nameMatch = /name="([^"]+)"/.exec(part);
    if (!nameMatch) continue;
    if (/filename="/.test(part)) {
      if (nameMatch[1] === "video") {
        const fnMatch = /filename="([^"]*)"/.exec(part);
        hasVideo = Boolean(fnMatch && fnMatch[1]);
      }
      continue;
    }
    const idx = part.indexOf("\r\n\r\n");
    if (idx !== -1) {
      fields[nameMatch[1]] = part.slice(idx + 4).replace(/\r\n$/, "");
    }
  }
  return { fields, hasVideo };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const server = http.createServer(async (req, res) => {
  const send = (code, obj, type = "application/json", headers = {}) => {
    res.writeHead(code, { "Content-Type": type, ...headers });
    res.end(type === "application/json" ? JSON.stringify(obj) : obj);
  };

  const url = (req.url || "").split("?")[0];

  // playable sample video standing in for presigned R2 URLs
  if (req.method === "GET" && url === "/sample-video.mp4") {
    if (!fs.existsSync(SAMPLE_VIDEO)) return send(404, { error: "no sample video" });
    res.writeHead(200, {
      "Content-Type": "video/mp4",
      "Content-Length": fs.statSync(SAMPLE_VIDEO).size,
      "Access-Control-Allow-Origin": "*",
    });
    fs.createReadStream(SAMPLE_VIDEO).pipe(res);
    return;
  }

  // ---------- auth ----------
  if (req.method === "POST" && url === "/auth/register") {
    const body = await drainBody(req);
    let data = {};
    try {
      data = JSON.parse(body.toString("utf8"));
    } catch {
      /* ignore */
    }
    const username = (data.username || "").trim();
    const password = data.password || "";
    if (!USERNAME_RE.test(username)) {
      return send(400, {
        error:
          "Username must be 3–32 characters, letters, numbers, or underscores only.",
      });
    }
    if (password.length < MIN_PASSWORD_LEN) {
      return send(400, {
        error: `Password must be at least ${MIN_PASSWORD_LEN} characters.`,
      });
    }
    if (store.users.some((u) => u.username.toLowerCase() === username.toLowerCase())) {
      return send(409, { error: "That username is already taken." });
    }
    const user = {
      id: store.nextUserId++,
      username,
      password_hash: hashPassword(password),
      created_at: new Date().toISOString(),
    };
    store.users.push(user);
    saveData();
    const token = crypto.randomUUID();
    sessions.set(token, user.id);
    return send(201, { user: publicUser(user) }, "application/json", {
      "Set-Cookie": sessionCookie(token),
    });
  }

  if (req.method === "POST" && url === "/auth/login") {
    const body = await drainBody(req);
    let data = {};
    try {
      data = JSON.parse(body.toString("utf8"));
    } catch {
      /* ignore */
    }
    const username = (data.username || "").trim();
    const password = data.password || "";
    const user = store.users.find(
      (u) => u.username.toLowerCase() === username.toLowerCase()
    );
    if (!user || !verifyPassword(password, user.password_hash)) {
      return send(401, { error: "Incorrect username or password." });
    }
    const token = crypto.randomUUID();
    sessions.set(token, user.id);
    return send(200, { user: publicUser(user) }, "application/json", {
      "Set-Cookie": sessionCookie(token),
    });
  }

  if (req.method === "POST" && url === "/auth/logout") {
    const token = parseCookies(req)["atlas_session"];
    if (token) sessions.delete(token);
    return send(200, { ok: true }, "application/json", {
      "Set-Cookie": clearCookie(),
    });
  }

  if (req.method === "GET" && url === "/auth/me") {
    const uid = currentUserId(req);
    const user = uid ? store.users.find((u) => u.id === uid) : null;
    return send(200, { user: user ? publicUser(user) : null });
  }

  if (req.method === "GET" && url === "/pro-clips") {
    if (!currentUserId(req)) return send(401, { error: "authentication required" });
    return send(200, listProClips());
  }

  // ---------- history (all require auth + ownership) ----------
  if (url === "/history" && req.method === "GET") {
    const uid = currentUserId(req);
    if (!uid) return send(401, { error: "authentication required" });
    const items = store.analyses
      .filter((a) => a.user_id === uid)
      .sort((a, b) => b.id - a.id)
      .map((a) => ({
        id: a.id,
        kind: a.kind,
        created_at: a.created_at,
        original_filename: a.original_filename,
        shot_type: a.shot_type,
        comparison_pro: a.comparison_pro,
        summary: a.summary ?? null,
      }));
    return send(200, { items });
  }

  const histMatch = /^\/history\/(\d+)$/.exec(url);
  if (histMatch) {
    const uid = currentUserId(req);
    if (!uid) return send(401, { error: "authentication required" });
    const id = Number(histMatch[1]);
    const idx = store.analyses.findIndex((a) => a.id === id && a.user_id === uid);
    if (idx === -1) return send(404, { error: "not found" });

    if (req.method === "GET") {
      const a = store.analyses[idx];
      const payload = JSON.parse(JSON.stringify(a.payload));
      payload.video_url = `http://localhost:${PORT}/sample-video.mp4`;
      return send(200, {
        id: a.id,
        kind: a.kind,
        created_at: a.created_at,
        original_filename: a.original_filename,
        shot_type: a.shot_type,
        comparison_pro: a.comparison_pro,
        payload,
      });
    }
    if (req.method === "DELETE") {
      store.analyses.splice(idx, 1);
      saveData();
      return send(200, { ok: true });
    }
  }

  if (req.method !== "POST") return send(404, { error: "not found" });

  // The analysis + coaching endpoints now require a logged-in user.
  const authedUserId = currentUserId(req);
  if (!authedUserId) return send(401, { error: "authentication required" });

  function recordAnalysis({ kind, payload, originalFilename, shotType, comparisonPro, summary }) {
    const rec = {
      id: store.nextAnalysisId++,
      user_id: authedUserId,
      kind,
      created_at: new Date().toISOString(),
      original_filename: originalFilename ?? null,
      shot_type: shotType ?? null,
      comparison_pro: comparisonPro ?? null,
      summary: summary ?? null,
      payload,
    };
    store.analyses.push(rec);
    saveData();
    return rec.id;
  }

  if (req.url === "/coaching-tips" || url === "/coaching-tips") {
    const body = await drainBody(req);
    let payload = null;
    try {
      payload = JSON.parse(body.toString("utf8"));
    } catch {
      /* fallthrough */
    }
    if (!payload?.results) {
      return send(400, { error: "expected JSON body with a 'results' field" });
    }
    console.log("POST /coaching-tips", payload.shot_type, payload.comparison_pro);
    try {
      const coaching = await generateCoachingTips(
        payload.results,
        payload.shot_type ?? "shot",
        payload.comparison_pro ?? "a professional"
      );
      return send(200, coaching);
    } catch (err) {
      console.error("coaching failed:", err.message);
      return send(502, { error: `coaching generation failed: ${err.message}` });
    }
  }

  const body = await drainBody(req);
  const { fields, hasVideo } = parseFormFields(body, req.headers["content-type"]);
  console.log(`${req.method} ${req.url}`, fields, `video=${hasVideo}`);

  if (!hasVideo) {
    return send(400, { error: "no video file provided (expected form field 'video')" });
  }

  if (req.url === "/process-tennis-video") {
    await sleep(4000); // simulate slow synchronous processing
    const payload = {
      net_clearance: 14.2,
      n_contacts: 6,
      fh_percent: 63.4,
      bh_percent: 36.6,
      key: `process_tennis_video/${crypto.randomUUID()}.mp4`,
      video_url: `http://localhost:${PORT}/sample-video.mp4`,
    };
    recordAnalysis({
      kind: "session",
      payload,
      originalFilename: fields.filename ?? null,
      summary: {
        net_clearance: payload.net_clearance,
        n_contacts: payload.n_contacts,
        fh_percent: payload.fh_percent,
        bh_percent: payload.bh_percent,
      },
    });
    return send(200, payload);
  }

  if (req.url === "/process-tennis-shot-analysis") {
    await sleep(2500);
    const shotType = fields.shot_type;
    const pro = fields.comparison_pro;
    const available = listProClips();
    // Mimics the real backend's 400 on an unrecognized shot_type/comparison_pro.
    if (!available[shotType] || !available[shotType].includes(pro)) {
      return send(400, {
        error: `invalid comparison_pro '${pro}' for shot_type '${shotType}'`,
      });
    }
    const payload =
      shotType === "serve" ? serveResults() : groundstrokeResults(shotType);
    const velocity = payload.results?.velocity ?? {};
    recordAnalysis({
      kind: "comparison",
      payload,
      shotType,
      comparisonPro: pro,
      summary: {
        avg_velocity_diff: velocity.average_difference,
        peak_velocity_diff: velocity.peak_difference,
      },
    });
    return send(200, payload);
  }

  send(404, { error: "not found" });
});

server.listen(PORT, () => {
  console.log(`Mock Atlas Motion backend on http://localhost:${PORT}`);
  console.log(`Gemini key configured: ${GEMINI_API_KEY ? "yes" : "NO"}`);
});
