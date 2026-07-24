# EliteForm

EliteForm is a computer-vision backend that analyzes tennis video. Upload a clip and it will detect the player, track the ball, listen for racket contact, classify strokes (forehand/backhand), measure net clearance, and — for single-shot clips — compare the player's mechanics (wrist speed, joint angles, serve toss drift) against a professional's.

The backend is a Flask app. There is currently **no frontend** — this document explains (1) how to build one against the existing API, and (2) exactly how the backend works internally, in enough depth that you shouldn't need to re-read the source to reason about it.

---

## Table of contents

- [Repository layout](#repository-layout)
- [Running the backend](#running-the-backend)
- [Part 1: Building a frontend](#part-1-building-a-frontend)
  - [API contract](#api-contract)
  - [Cloudflare R2 video storage](#cloudflare-r2-video-storage)
  - [Gaps you will need to work around](#gaps-you-will-need-to-work-around)
  - [Suggested frontend architecture](#suggested-frontend-architecture)
- [Part 2: Backend architecture (comprehensive)](#part-2-backend-architecture-comprehensive)
  - [High-level data flow](#high-level-data-flow)
  - [`api/app.py` — the Flask entrypoint](#apiapppy--the-flask-entrypoint)
  - [`ball_tracking` — `Tracker`](#ball_tracking--tracker)
  - [`contact_detection` — `ContactDetector`](#contact_detection--contactdetector)
  - [`net_clearance` — `NetClearance`](#net_clearance--netclearance)
  - [`shot_classification` — `ShotClassifier`, the model, and training](#shot_classification--shotclassifier-the-model-and-training)
  - [`shot_analysis` — `GroundStrokeAnalysis` and `ServeAnalysis`](#shot_analysis--groundstrokeanalysis-and-serveanalysis)
  - [Models and assets](#models-and-assets)
  - [Known limitations and gotchas](#known-limitations-and-gotchas)

---

## Repository layout

```
api/
  app.py                     Flask app — the only HTTP entrypoint
  r2.py                      Cloudflare R2 upload helper (upload_video)
  run_api.sh                 launches `python3 -m api.app`
  runs/                      annotated output videos from /process-tennis-video, deleted locally after upload to R2
  testing_scripts/           curl scripts that exercise each endpoint
sports/tennis/
  ball_tracking/             YOLO + ByteTrack ball tracker
  contact_detection/         audio-RMS-based contact (ball-hit) detector
  net_clearance/             net localization + ball-to-net distance
  shot_classification/       forehand/backhand classifier (model + training pipeline)
  shot_analysis/             player-vs-pro comparison (groundstrokes + serves)
  models/                    all trained/pretrained model weights
data/shot_classification/    raw labeled clips used to train the shot classifier
pro_videos/tennis/           reference pro clips, by shot type, used for comparison
test_videos/                 sample clips for manual testing
heatmaps/                    generated swing-path images (output of shot_analysis)
scripts/                     one-off utility scripts (CoreML export, fps conversion)
venv/                        checked-in virtualenv (no requirements.txt exists)
```

## Running the backend

There is no `requirements.txt`/`pyproject.toml` — dependencies are pre-installed into the checked-in `venv/`. From the repo root:

```bash
source venv/bin/activate
bash api/run_api.sh          # equivalent to: python3 -m api.app
```

The server starts on `0.0.0.0:5001` in Flask debug mode. All model/file paths in the codebase are relative, so it must always be launched **from the repository root**, not from inside `api/`.

`api/r2.py` loads Cloudflare R2 credentials from a `.env` file at the repo root (via `python-dotenv`) and reads them eagerly at import time, so a `.env` missing any of these will crash the server on startup with a `KeyError`, not a lazy runtime error:

```
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=
R2_ENDPOINT=          # R2's S3-compatible API endpoint, e.g. https://<account_id>.r2.cloudflarestorage.com
R2_TOKEN_VALUE=
```

Note `R2_ENDPOINT` is the private S3 API endpoint used for uploads (`boto3` talks to this), **not** a browser-facing URL — see [Cloudflare R2 video storage](#cloudflare-r2-video-storage) for the separate public URL a frontend needs.

Manual smoke tests (also run from repo root):

```bash
bash api/testing_scripts/tennis_process_video.sh      # POST /process-tennis-video
bash api/testing_scripts/tennis_shot_analysis.sh      # POST /process-tennis-shot-analysis
```

Both scripts dump the response body to `response.json` and headers to `headers.txt` at the repo root — useful references for exact response shape.

---

## Part 1: Building a frontend

### API contract

The Flask app exposes exactly two routes, both POST, both `multipart/form-data`. There is no authentication, no versioning, and no OpenAPI spec — this section *is* the spec.

#### `POST /process-tennis-video`

Full-video pipeline: runs shot classification, contact detection, ball tracking, and net-clearance measurement over every frame, and returns aggregate statistics for the whole clip.

**Request** (`multipart/form-data`):

| field   | type | required | notes                          |
|---------|------|----------|---------------------------------|
| `video` | file | yes      | any video OpenCV/ffmpeg can read |

**Response** `200`:

```json
{
  "net_clearance": 14.2,
  "n_contacts": 6,
  "fh_percent": 63.4,
  "bh_percent": 36.6,
  "key": "process_tennis_video/c6a65ca2-70bd-44ab-b794-b12c9b55ddc2.mp4"
}
```

- `net_clearance` — average ball-to-net clearance across the clip, **in inches**. Only meaningful if the net was visually detected and the ball crossed near it at least once.
- `n_contacts` — number of racket/ball contacts detected via audio, across the whole clip.
- `fh_percent` / `bh_percent` — percentage of shot-classifier inferences that were forehand vs. backhand (sums to ~100).
- `key` — the R2 object key for the **annotated output video** (ball-tracking boxes + shot-label overlay burned into the frames). See [Cloudflare R2 video storage](#cloudflare-r2-video-storage) for how to turn this into a playable URL.

**Response** `400`: `{"error": "no video file provided (expected form field 'video')"}` or `{"error": "empty filename"}`.

The annotated output video is written server-side to `api/runs/<original_filename>_output.mp4`, uploaded to R2 (see below), and then **deleted from local disk** — `api/runs/` is a transient working directory now, not persistent storage. There is still no route to fetch it directly from the Flask server; R2 is the only place it lives after the request completes.

This endpoint is slow: it runs YOLO person detection, YOLO ball tracking, MediaPipe pose landmarking, and a Keras inference pass on essentially every frame, plus an `ffmpeg` subprocess for audio extraction, all synchronously inside the request. Expect multiple seconds of processing per second of video.

#### `POST /process-tennis-shot-analysis`

Single-shot pipeline: compares one short clip of the player against a reference pro clip.

**Request** (`multipart/form-data`):

| field            | type   | required | notes |
|------------------|--------|----------|-------|
| `video`          | file   | yes      | a short clip containing exactly one shot |
| `shot_type`      | string | yes      | `"forehand"`, `"backhand"`, or `"serve"` — see caveat below |
| `comparison_pro` | string | yes      | name of the reference clip, **without** `.mp4` |

Reference clips currently available (the file must exist at `pro_videos/tennis/<shot_type>/<comparison_pro>.mp4` or the request will throw):

| `shot_type` | valid `comparison_pro` values |
|-------------|-------------------------------|
| `forehand`  | `Dimitrov` |
| `backhand`  | `Sinner` |
| `serve`     | `Alcaraz` |

> **Caveat:** the backend does not validate `shot_type`. Any value other than the literal strings `"forehand"` or `"backhand"` falls through to the serve-analysis code path (see [`api/app.py`](#apiapppy--the-flask-entrypoint) below). Always send exactly `"forehand"`, `"backhand"`, or `"serve"`.

Both branches (forehand/backhand via `GroundStrokeAnalysis`, and `serve`/anything else via `ServeAnalysis`) wrap the analysis dict under `"results"`, alongside the R2 `"key"` for the **player's uploaded clip** (not an annotated output — this endpoint doesn't burn any overlay into a video, it just re-uploads what was sent in):

**Response** `200` (serve — `ServeAnalysis`, and currently any non-`"forehand"`/`"backhand"` `shot_type`):

```json
{
  "results": {
    "shot_type": "serve",
    "velocity": {
      "average_difference": 3.11,
      "peak_difference": 15.34,
      "player": { "average": 24.05, "peak": 59.34 },
      "pro":    { "average": 20.94, "peak": 44.00 }
    },
    "joint_angles": {
      "left_elbow":   { "player_average": 129.35, "pro_average": 137.05, "difference": -7.70, "player_range": [51.88, 175.97], "pro_range": [37.62, 177.79] },
      "left_shoulder": { "...": "..." },
      "left_knee":     { "...": "..." },
      "right_elbow":   { "...": "..." },
      "right_shoulder":{ "...": "..." },
      "right_knee":    { "...": "..." }
    },
    "toss": {
      "drift_difference_ft": 0.8,
      "player": { "toss_drift_ft": 2.1 },
      "pro": { "toss_drift_ft": 1.3 }
    },
    "visuals": {
      "player_swing_path": "heatmaps/<uuid>_player.png",
      "pro_swing_path": "heatmaps/<uuid>_pro.png",
      "player_frame_w": 1080,
      "pro_frame_w": 1920
    }
  },
  "key": "process_tennis_shot_analysis/9e2d1e0a-....mp4"
}
```

(`joint_angles` and `visuals` shapes are unchanged from before R2 was added — see `response.json` at repo root for a full real capture. The `toss` block and `visuals.*_frame_w` fields are `ServeAnalysis`-only; `GroundStrokeAnalysis` (forehand/backhand) returns the same shape without them, still nested under `"results"`.)

`toss_drift_ft` (and the diff) can be `null` if the ball tracker never found a usable toss trajectory in the clip — the frontend must handle `null` here.

Velocity units are **mph**; angles are **degrees**; all `"difference"`/`"*_difference"` fields are `player - pro` (positive means the player's value is higher).

`visuals.*_swing_path` values are still **filesystem paths relative to the backend's working directory**, not URLs, and are **not** uploaded to R2 — only videos go through `upload_video`. A frontend cannot load these heatmap PNGs until a static-file route is added (see [Gaps](#gaps-you-will-need-to-work-around) #1, still open for images). This is different from the top-level `key` field, which *is* R2-backed — see the next section.

### Cloudflare R2 video storage

Both endpoints now upload a video to Cloudflare R2 and return an object `"key"` in the JSON response (see the response examples above). This is handled by `api/r2.py`:

```python
def upload_video(local_path: str, folder: str) -> str:
    key = f"{folder}/{uuid.uuid4()}.mp4"
    client.upload_file(local_path, BUCKET, key)
    return key
```

- `/process-tennis-video` uploads the **annotated output video** (ball-tracking boxes + shot-label overlay) under the `process_tennis_video/` folder, after the frame-processing loop finishes and before the local copy in `api/runs/` is deleted.
- `/process-tennis-shot-analysis` uploads the **raw player clip as submitted** (no overlay is drawn for this endpoint) under the `process_tennis_shot_analysis/` folder.
- In both cases `key` is a fresh `<folder>/<uuid4>.mp4` — random per upload, not derived from the original filename, and nothing currently deletes old objects from the bucket (same "never cleaned up" pattern as `heatmaps/` and, previously, `api/runs/`).

**Using `key` in a frontend:** `key` is an R2 **object key**, not a URL — it only tells you where the object lives inside the bucket, not how to reach it over HTTP. To actually load or `<video>`-play the file in a browser, the bucket needs a public-facing base URL (either R2's `pub-<hash>.r2.dev` dev URL with public access enabled on the bucket, or a custom domain mapped to the bucket via Cloudflare), and the frontend joins that base URL with `key`:

```js
const R2_PUBLIC_BASE_URL = "https://<your-r2-public-base>"; // e.g. a pub-*.r2.dev URL or custom domain
const videoUrl = `${R2_PUBLIC_BASE_URL}/${response.key}`;

// e.g. straight into a <video> element:
// <video src={videoUrl} controls />
```

This base URL is **not** the same as `R2_ENDPOINT` in `.env` (that's the private S3 API endpoint `boto3` uses to *upload*, and typically isn't configured for public reads) — it's a separate value you configure once the bucket (or a custom domain) is set to allow public access, and it currently isn't stored anywhere in this repo. The frontend is responsible for knowing it (e.g. as a build-time env var of its own) and concatenating it with whatever `key` a response returns.

### Gaps you will need to work around

The backend was built without a frontend in mind. Before or while building one, be aware of:

1. **Videos are covered by R2 now; heatmap images are not.** Both output/uploaded videos are R2-backed via the `key` field (see [Cloudflare R2 video storage](#cloudflare-r2-video-storage) above) — a frontend can load them today, given a public R2 base URL. Swing-path heatmap PNGs (`visuals.*_swing_path`) are still bare filesystem paths under `heatmaps/` on the server's disk with no upload step and no serving route (no `send_from_directory`, no `static_folder`). A frontend cannot display these images until they're either uploaded to R2 like the videos are, or a route like `GET /heatmaps/<filename>` is added to `app.py`.
2. **No CORS headers.** `flask-cors` is not installed/configured. A browser-based frontend on a different origin (e.g. `localhost:3000` calling `localhost:5001`) will be blocked unless CORS is added or requests are proxied.
3. **Fully synchronous, blocking requests.** There's no job queue, no websocket, no polling endpoint, and no progress reporting. The HTTP request for `/process-tennis-video` simply blocks until the whole video is processed (this can take a long time for anything more than a few seconds of footage). The frontend must show an indeterminate loading state and use a generous client-side timeout — do not assume a fast response.
4. **Flask dev server, single-worker.** Running via `app.run(debug=True)` means one request is handled at a time; concurrent uploads from multiple users will queue up behind each other. Fine for a demo/single-user frontend, not for production concurrency.
5. **No database.** Nothing is stored in a database — every response is computed fresh. Videos now persist in R2 (unbounded — nothing deletes old objects) and are addressable by the returned `key`, but there's no index of past uploads/analyses anywhere. A "history" or "past analyses" feature in the frontend has no backend support today; you'd need to add one (e.g. record each response's `key` plus its stats in a database as the frontend receives them, since the backend itself doesn't persist that mapping).
6. **Possible 500s on edge-cases**, not currently guarded against:
   - `/process-tennis-video`: if the shot classifier never accumulates a full 60-frame buffer (very short clips) `n_sc_inferences` stays `0` and the `fh_percent`/`bh_percent` division raises `ZeroDivisionError`. Similarly, `net_clearance.get_final_clearance()` divides by `total_iterations`, which is `0` if the net was never detected or the ball never tracked.
   - `/process-tennis-shot-analysis`: `ServeAnalysis.analyze_shot` explicitly raises `RuntimeError` if fewer than 2 wrist detections are found, and `FileNotFoundError` if the video can't be opened. `GroundStrokeAnalysis` doesn't guard the equivalent case — a clip with no detected pose will raise on `np.mean` of an empty array.
   
   None of these are turned into clean `4xx` JSON error responses by Flask's default error handler — they'll surface as HTML `500` pages. A frontend should treat any non-`200`/non-JSON response as "analysis failed, try a clearer clip."
7. **`comparison_pro` options are hardcoded to one clip per shot type** (`Dimitrov`, `Sinner`, `Alcaraz` — see table above). There's no endpoint to discover this list programmatically; the frontend must hardcode it too, or the backend should grow a `GET /pro-videos` listing endpoint.

### Suggested frontend architecture

A minimal, honest-to-the-current-backend frontend needs three screens/states:

1. **Upload** — a file picker for the video, plus (for the shot-analysis flow) a shot-type selector (`forehand` / `backhand` / `serve`) and a pro-comparison selector populated from the hardcoded table above. Two clearly separated flows map 1:1 to the two endpoints — don't try to unify them, their inputs and outputs are different shapes.
2. **Processing** — an indeterminate progress indicator (spinner/skeleton), since there's no real progress signal from the backend. Set a long client-side request timeout (minutes, not seconds) for `/process-tennis-video` in particular.
3. **Results**:
   - For `/process-tennis-video`: show the four numeric stats, plus the annotated output video itself — playable today via `${R2_PUBLIC_BASE_URL}/${key}` (see [Cloudflare R2 video storage](#cloudflare-r2-video-storage)).
   - For `/process-tennis-shot-analysis`: render the velocity comparison (bar chart or simple side-by-side numbers), the six joint angles (player vs. pro, e.g. a radar/spider chart or a table with the `difference` column highlighted), the toss-drift stat for serves, and — once heatmap images get a serving route (still open, see Gap #1) — the two swing-path heatmap PNGs side by side. Response fields are nested under `"results"` now (see the response example above).

Any HTTP client works (`fetch`, `axios`); send the form fields as `FormData`, not JSON, since both endpoints read `request.files` / `request.form`.

---

## Part 2: Backend architecture (comprehensive)

### High-level data flow

```
                         ┌───────────────────────┐
  video upload  ──POST──▶│      api/app.py        │
                         └───────────┬────────────┘
                                     │
                 ┌───────────────────┼─────────────────────┐
                 │                                          │
     /process-tennis-video                    /process-tennis-shot-analysis
                 │                                          │
   per-frame loop over the clip:              shot_type in {forehand, backhand}?
   ┌─────────────┴──────────────┐               │                    │
   │ ShotClassifier.classify_   │            GroundStrokeAnalysis   ServeAnalysis
   │   shots()  → fh/bh label   │               │                    │
   │ ContactDetector.detect_    │       both: run analyze_shot() once on the
   │   contact() → bool         │       player clip and once on the pro clip,
   │ Tracker.track() → ball box │       then diff velocity + 6 joint angles
   │ NetClearance.*  → inches   │       (ServeAnalysis additionally tracks the
   └─────────────┬──────────────┘        ball for toss drift)
                 │                                          │
        aggregate counts/percentages          heatmap PNGs written to heatmaps/,
        → JSON response                       diff JSON → response
```

Every request is stateless at the HTTP layer: `app.py` instantiates fresh `ShotClassifier`, `ContactDetector`, `Tracker`, `NetClearance` (or fresh `GroundStrokeAnalysis`/`ServeAnalysis`) objects on every call. Nothing is cached or reused across requests — every request reloads the YOLO weights, the Keras model, and the MediaPipe pose landmarker from disk. This is simple and request-isolated, but means per-request startup cost is nontrivial (multiple model loads) on top of the actual video processing.

### `api/app.py` — the Flask entrypoint

The whole HTTP surface. Two routes:

**`process_tennis_video()`** (`POST /process-tennis-video`):

1. Instantiates `ShotClassifier`, `ContactDetector`, `Tracker`, `NetClearance`.
2. Validates the `video` form field is present and non-empty, saves it to `tmp/<secure_filename>`.
3. Creates a MediaPipe `PoseLandmarker` instance from `classifier.options` (built once, reused for every frame in the loop — this is the *video-mode* landmarker, which expects monotonically increasing timestamps, hence the shared instance).
4. Opens the video with OpenCV, reads `fps`/`total_frames`, hardcodes the working/output frame size to `(640, 360)`, and opens an `mp4v` `VideoWriter` pointed at `api/runs/<name>_output.mp4`.
5. Shells out to `ffmpeg` to extract mono 16kHz PCM audio to `tmp/audio.wav`, loads it with `librosa`, and hands it to `contact_detector.set_audio()`.
6. Loops every frame (`while True: ret, frame = cap.read()`), for each frame:
   - Resizes to 640×360; keeps an unmodified copy (`orig_frame`) for net detection (since drawing overlays on `frame` would corrupt edge detection).
   - Runs `classifier.classify_shots(...)`. If it returns a label, increments the corresponding counter in `occurences` and `n_sc_inferences`.
   - Determines the on-screen text: shows the *last* prediction as long as it was made within 40 frames, otherwise shows `"neutral"`; burns it into the frame with `cv.putText` (skipped for `"neutral"`).
   - Runs `contact_detector.detect_contact(frame_idx)`; increments `n_contacts` on a truthy return.
   - Runs `tracker.track(frame)`, which both draws the ball's bounding box onto `frame` and returns its coordinates.
   - Lazily locates the net exactly once (`if clearance.net is None: clearance.locate_net(orig_frame)`) and lazily computes `meters_per_pixel` exactly once, once `classifier.player_height_pixels` has been populated by the shot classifier's first successful person detection.
   - If the ball was found this frame, computes its center and calls `clearance.calculate_net_clearance(...)`, which accumulates a running sum.
   - Writes the annotated frame to the output video.
7. After the loop: releases the reader/writer, computes `avg_clearance` from the accumulator, computes `fh_percent`/`bh_percent` from `occurences`/`n_sc_inferences`, uploads the annotated output video to R2 via `upload_video(local_path=output_path, folder="process_tennis_video")` (getting back an object `key`), deletes the temp input video, temp audio file, **and** the local output video (now that it's in R2), and returns the four stats plus `key` as JSON.

**`process_tennis_shot_analysis()`** (`POST /process-tennis-shot-analysis`):

1. Reads `shot_type` and `comparison_pro` from the form, validates + saves the `video` file the same way as above.
2. Uploads the saved input video to R2 via `upload_video(local_path=input_path, folder="process_tennis_shot_analysis")` (getting back an object `key`) — this happens before branching on `shot_type`, so it runs (and the video is retained in R2) even on the paths that go on to fail below.
3. **Branches on `shot_type`:** if it is exactly `"forehand"` or `"backhand"`, builds a `GroundStrokeAnalysis(shot_type, comparison_pro)` and calls `run_analysis(player_video_path, pro_video_path)` where `pro_video_path = f"pro_videos/tennis/{shot_type}/{comparison_pro}.mp4"`. **Any other value of `shot_type`** (including `"serve"`, but also typos or `None`) falls into the `else` branch, which builds `ServeAnalysis(comparison_pro)` and calls `run_analysis` with the same path template — meaning an invalid `shot_type` string still gets baked into the pro-video path and will fail with a file-not-found style error rather than a clean validation error. Both branches then delete the temp input file and return `jsonify({"results": results, "key": key}), 200`.

### `ball_tracking` — `Tracker`

`sports/tennis/ball_tracking/tracker.py`

Wraps a YOLO model fine-tuned to detect the tennis ball (`sports/tennis/models/tracker.pt`), run through Ultralytics' built-in ByteTrack tracker for temporal association.

- **Device selection**: `mps` if `torch.backends.mps.is_available()` (Apple Silicon GPU), else `cpu`. No CUDA branch.
- **Tracker config**: `sports/tennis/ball_tracking/optimized_tracker.yaml` — a hand-tuned ByteTrack config (`track_high_thresh=0.25`, `track_low_thresh=0.1`, `new_track_thresh=0.25`, `track_buffer=5`, `match_thresh=0.7`, `fuse_score=True`). Per the git history, these thresholds were specifically tuned to cut inference time roughly 75–140ms → 15–20ms versus the stock config.
- **`track(frame)`**: resizes the frame to 640×360, calls `model.track(..., persist=True, conf=0.2, stream=True)` and pulls exactly one result via `next()` on the generator (i.e. it processes one frame per call, using `stream=True` purely to avoid Ultralytics buffering results for frames it doesn't have yet — not for multi-frame batching). If no boxes are found, returns `(frame, None)`. Otherwise takes the **first** detected box (`boxes.xyxy[0]` — no explicit "highest confidence" selection since typically only one ball is expected), draws a green rectangle on the frame, and returns `(frame, (x1, y1, x2, y2))`.
- Explicitly runs `gc.collect()` and `torch.mps.empty_cache()` after every single frame — a deliberate (if aggressive) mitigation for MPS memory growth during long video loops.

### `contact_detection` — `ContactDetector`

`sports/tennis/contact_detection/contact_detection.py`

Detects racket/ball contact **purely from audio**, independent of any visual signal. Conceptually: a ball/racket impact produces a short, sharp spike in acoustic energy above the clip's baseline.

- `set_audio(audio, sr)` stores the full waveform and sample rate (populated once per request in `app.py` from the extracted `tmp/audio.wav`).
- `get_audio_at_frame(frame_idx)` converts a video frame index to an audio sample window: assumes **30fps** (`time = frame_idx / 30`, hardcoded, not derived from the video's actual fps), then takes a ±25ms window (`window = 0.05 * sr`, split evenly) around that timestamp.
- `calculate_avg_energy()` computes the RMS energy of the **entire clip's audio** and sets a threshold `min_energy = avg_energy + 0.075` (a fixed absolute offset, not a multiplicative factor — this recomputes from scratch on every single call to `detect_contact`, which is wasteful but harmless since the audio doesn't change).
- `detect_contact(frame_idx)`: computes the RMS energy of the frame's audio window; if it's at or above `min_energy` **and** at least `cooldown_frames` (`0.15 * 30 = 4.5 → 4` frames) have passed since the last detected hit, marks a contact, updates `last_hit_frame`, increments `n_contacts`, and returns `True`. This cooldown prevents one loud hit from being counted multiple times across overlapping windows.

### `net_clearance` — `NetClearance`

`sports/tennis/net_clearance/clearance.py`

Estimates how far above the net the ball passes, in inches, averaged over the whole clip.

- **`locate_net(frame)`** (called once, lazily, on the first frame): grayscales the frame, runs Canny edge detection (`thresholds 500/600`), then a probabilistic Hough transform (`HoughLinesP`) to find line segments. Candidate lines are filtered to ones whose **y-coordinates fall within a fixed vertical band around the frame's vertical center** (`h//2 ± 80`, i.e. rows 100–260 of a 360-tall frame) — this assumes the net is always roughly centered vertically in frame, which only holds for a specific camera angle/setup (e.g. baseline-facing broadcast-style shots). Among the candidates, the **longest** line segment is kept as "the net." If no candidate survives, `self.net` stays `None` and clearance is never computed.
- **`calculate_meters_per_pixel(player_height_pixel, player_height_meters=1.7018)`**: derives a pixel→meter scale factor from the assumption that the tracked player is exactly 1.7018m tall (≈5'7") and occupies `player_height_pixel` pixels vertically in frame. This is a **global constant assumption**, not personalized per player — real players taller or shorter than this will get systematically skewed distance/velocity numbers throughout the codebase (this same constant and pattern is reused in `GroundStrokeAnalysis` and `ServeAnalysis`).
- **`distance_point_to_line`**: standard point-to-line-segment-extended-as-infinite-line distance formula (`|Ax₀+By₀+C| / √(A²+B²)` where the line is defined by two points) — note this treats the net as an infinite line, not a bounded segment, so a ball detected far to the side (outside the actual net's horizontal extent) would still register a "clearance" value.
- **`calculate_net_clearance(ball_cx, ball_cy)`**: computes the ball's distance to the net line, converts pixels → meters → inches (`× 39.3701`), and accumulates into a running sum/count (called once per frame where the ball was detected, from `app.py`'s main loop).
- **`get_final_clearance()`**: `running_total / total_iterations` — the average clearance across every frame the ball was seen. Divides by zero if the ball was never detected or the net was never located (see [Gaps](#gaps-you-will-need-to-work-around) #6).

### `shot_classification` — `ShotClassifier`, the model, and training

This is the most complex subsystem: a real-time-ish, streaming, sequence-based binary classifier (forehand vs. backhand) driven by pose keypoints.

#### Runtime class: `sports/tennis/shot_classification/shot_classification.py` (`ShotClassifier`)

Constructor loads three models: a MediaPipe `PoseLandmarker` (config, not instance — the instance is created separately in `app.py` since it needs to be a long-lived video-mode session), the trained Keras sequence model (`binary_shot_classifier.keras`, deserialized with the custom `sc_model.ShotClassifier` class), and a YOLO person detector (`yolo11n.pt`, the stock COCO model, used only for its `person` class).

Per-frame pipeline in **`classify_shots(frame, frame_idx, landmarker)`**:

1. **Person detection** — runs YOLO (`classes=[0]` = person only, `conf=0.3`) on the frame. (Note: `if self.prev_pose is None or frame_idx % 1 == 0` is always true since anything `% 1` is `0` — despite the comment describing this as an "every-3-frame throttle," the code currently runs YOLO on **every** frame; the caching branch (`self.prev_pose`) is effectively dead code as written.) Among all detected people, keeps the one with the **largest bounding-box area** as the player.
2. Records `player_height_pixels` once, from the very first successful detection's box height — used later by `NetClearance` for the meters-per-pixel conversion (via `app.py`, which reads `classifier.player_height_pixels`).
3. Pads the player's bounding box by 35% on each side (`pad_w/pad_h = 0.35 × box dimension`) and crops that region out of the frame — pose landmarking runs on this crop, not the full frame, both for accuracy (larger relative subject) and to avoid picking up a second person's pose.
4. **Pose landmarking** — same `% 1` always-true pattern as step 1 (again effectively runs every frame despite variable names suggesting throttling/caching). Converts the crop to RGB, wraps it as a MediaPipe `Image`, and calls `landmarker.detect_for_video(mp_img, timestamp_ms)` where `timestamp_ms` is derived assuming **30fps** (`frame_idx / 30 * 1000`, hardcoded regardless of actual video fps — same assumption as `ContactDetector`). If no pose is found, falls back to the previous frame's raw MediaPipe result (`prev_landmarks_mp_result`), and if there's no fallback either, the frame is skipped (`classify_shots` returns `None`).
5. **`convert_landmarks`** flattens MediaPipe's result into a plain `(33, 3)` numpy array (x, y, z per landmark; 33 = MediaPipe's full-body pose landmark count).
6. **`extract_features(pose_frame, prev_pose_frame)`** builds the per-frame feature vector fed to the model:
   - Computes hip-center and shoulder-center as the midpoints of the left/right hip and shoulder landmarks.
   - `torso` = distance between those centers (a personal "unit of scale"), floored at `1e-6` to avoid division by zero.
   - **Normalized pose**: every landmark is re-expressed as `(landmark − hip_center) / torso` — this makes the representation invariant to the player's absolute position in frame and roughly invariant to their distance from the camera (i.e., their apparent size). All 33×3=99 values are appended to the feature vector.
   - **Velocity**: same normalization applied to the *previous* frame's pose, then `velocity = normalized_pose − prev_normalized_pose` (frame-to-frame delta in normalized space). Zeros if there's no previous frame. Another 99 values appended.
   - **Joint angles** (6 values, via `calculate_angle(a, b, c)` — the angle at vertex `b` between rays `b→a` and `b→c`, computed via the dot-product/arccos formula, in **radians** here unlike the degree version in `shot_analysis`): right elbow, left elbow, right knee, left knee, right shoulder-ish (hip-shoulder-elbow), left shoulder-ish.
   - **Wrist features** (8 values): raw normalized x/y/z for both wrists, plus the scalar speed (`‖velocity‖`) of each wrist specifically — wrist speed/position is presumably the single most discriminative signal for forehand vs. backhand.
   - Total feature vector length: 99 + 99 + 6 + 8 = **212** dimensions per frame.
7. The feature vector is appended to `self.shot_buffer` (a growing list), and `prev_pose_frame` is updated for next frame's velocity computation.
8. **Sliding-window inference**: once the buffer reaches `seq_len=60` frames, `process_buffer` takes the **last 60** feature vectors, adds a batch dimension, and runs them through the Keras model, which outputs a single sigmoid probability. `< 0.5` → `"forehand"`, `≥ 0.5` → `"backhand"`. `previous_prediction` and `last_pred_frame` are updated (used by `app.py` to decide how long to keep displaying a label on screen — 40 frames). The buffer is then **not cleared**, only trimmed by `slide_step=30` frames (`shot_buffer = shot_buffer[30:]`) — so inference re-runs every 30 frames on a 60-frame window that's half-overlapping with the previous one, giving smoother, more frequent predictions than a hard reset-every-60-frames approach would.
9. Returns `(output_class, probs)` where `output_class` is `None` until the buffer first fills up, and `probs` is `-1` as a sentinel when there's no prediction yet (a slightly unusual choice: `-1` is not a valid sigmoid output, used here purely as a "no prediction" flag rather than `None`, likely to keep the return type numeric for callers).

#### Model architecture: `sports/tennis/shot_classification/sc_model.py`

A Keras subclassed `Model` (not the functional API), consuming the 212-dim × 60-frame sequences described above:

```
Input (batch, 60, 212)
  → Dense(128, relu)                              # per-timestep projection
  → Conv1D(128, kernel=3, relu)                    # local temporal pattern 1
  → Conv1D(128, kernel=5, relu)                    # local temporal pattern 2 (stacked, not parallel)
  → BatchNormalization
  → Bidirectional(LSTM(128, return_sequences=True))# forward+backward temporal context, 256-wide output
  → MultiHeadAttention(4 heads, key_dim=64), self-attention (x attends to x)
  → residual add + LayerNormalization              # x = LayerNorm(x + attn(x))
  → GlobalAveragePooling1D                          # collapse the time dimension
  → Dense(256, relu)
  → Dropout(0.4)
  → Dense(1, sigmoid)                               # P(backhand)
```

This is effectively a small CNN-BiLSTM-Transformer hybrid: convolutions extract local motion patterns, the BiLSTM captures longer-range temporal structure in both directions, and self-attention lets the model weigh which frames in the 60-frame window matter most (e.g. the actual contact frame vs. the wind-up) before pooling to a single classification.

#### Training pipeline: `sports/tennis/shot_classification/training/`

- **`preprocess.py`** builds the training set from raw labeled clips in `data/shot_classification/{forehand,backhand}/*.mp4`. For each video: runs the same YOLO-crop → MediaPipe-landmark → `extract_features` pipeline as the runtime class (reusing an instantiated `ShotClassifier` purely for its helper methods and loaded models — `utils = ShotClassifier()`), chunking the resulting feature sequence into **non-overlapping** 60-frame windows (unlike the runtime's overlapping sliding window). Each full 60-frame chunk becomes one training example labeled by its source folder.
  - **Augmentation** (`augment_sequence`): time-warping (resample the sequence to a randomly stretched/compressed length between 0.85–1.15×, then resample back to 60 frames — simulates faster/slower swings), uniform amplitude scaling (0.85–1.15×), and additive Gaussian noise (std 0.005–0.03) — applied with an 80% chance of time-warping and always scale+noise.
  - **Mixup** (`mixup_sequences`): blends two same-class sequences with a Beta(0.4, 0.4)-sampled interpolation weight, biased (via `lam = max(lam, 1-lam)`) to stay closer to one parent than a mushy 50/50 average — applied with 35% probability per generated sample.
  - Each class is padded up to `TARGET_PER_CLASS = 2500` samples via augmentation/mixup of the original examples (looping through originals with `base_idx = i % n_orig`), so both classes end up balanced regardless of how many raw clips exist per class.
  - Final `X.npy` (`shape: [N, 60, 212]`) and `y.npy` (`shape: [N]`, 0=forehand/1=backhand) are shuffled together and saved to `sports/tennis/shot_classification/data/`.
- **`trainer.py`**: loads `X.npy`/`y.npy`, compiles `sc_model.ShotClassifier` with `binary_crossentropy` loss and Adam(1e-3), trains for up to 100 epochs with `batch_size=16` and a 10% validation split, using `EarlyStopping` (patience 15, restores best weights) and `ModelCheckpoint` (saves only the best-`val_loss` model) writing directly to `sports/tennis/models/binary_shot_classifier.keras` — i.e. **retraining overwrites the production model file in place**, there's no versioning.
- There's a second, seemingly superseded model file `sports/tennis/models/shot_classifier.keras` and an experiment script `shot_classification/experiments/contact_based_shot_classification.py` (contact-detection-gated classification: only run the classifier on a window centered on a detected audio contact, rather than continuously) — not wired into the live API, kept as exploratory/reference code.

### `shot_analysis` — `GroundStrokeAnalysis` and `ServeAnalysis`

`sports/tennis/shot_analysis/groundstroke_analysis.py` and `serve_analysis.py`. These analyze **one shot per clip** (as opposed to `ShotClassifier`'s continuous streaming over a whole rally) and produce a player-vs-pro comparison. The two classes are near-duplicates; `ServeAnalysis` is a superset that additionally tracks the ball for toss statistics. Both expose the same two-method shape: `analyze_shot(path)` (run once per video) and `run_analysis(player_video_path, pro_video_path)` (runs `analyze_shot` twice and diffs the results).

**`analyze_shot(path)`** — shared logic:

- Unlike the full-video pipeline, this does **not** resize frames to 640×360 — `ServeAnalysis` explicitly reads and preserves the native `frame_w`/`frame_h` (important since these short clips may come from different source resolutions than the main pipeline, and precise pixel measurements matter more for a single-shot comparison). `GroundStrokeAnalysis` also processes at native resolution (no `cv.resize` call in its loop).
- Every frame: runs YOLO person detection (`conf=0.2`), keeps the largest box as the player, computes `meters_per_pixel` once from the **first** frame's box height (same 1.7018m assumption as `NetClearance`), pads the box (35% in `GroundStrokeAnalysis`, 40% in `ServeAnalysis`) and crops it, then runs MediaPipe pose landmarking on the crop (fresh `PoseLandmarker.create_from_options` **per call to `analyze_shot`**, i.e. a new landmarker instance for the player clip and another new one for the pro clip — no state shared across the two).
- For every frame with a valid pose: records the **right wrist's** local (crop-relative, for velocity) and global (frame-pixel, for the swing-path plot) position, and computes six joint angles in **degrees** (`calculate_angle` here calls `np.degrees()`, unlike the radians version in `ShotClassifier`): right/left elbow, shoulder, knee.
- **Velocity**: takes frame-to-frame wrist displacement (`np.diff`), converts to per-frame distance, **filters out displacements ≤ 2px** (treated as pose jitter/noise rather than real motion), converts px→m→mph (`× meters_per_pixel × fps × 2.23694`), then **filters out any single-frame velocity ≥ 100mph** (treated as an impossible detection artifact — e.g. a pose landmark snapping between two different people). Reports both the mean (`"average"`) and 95th-percentile (`"peak"`) of what remains.
- **Swing path**: a blank image the size of the frame, with a small filled circle drawn at every recorded wrist position, saved as a PNG — visually, a "heatmap" of where the wrist traveled during the shot. `GroundStrokeAnalysis` additionally re-centers and scales this relative to the player's average bounding box (so the swing path is normalized regardless of how large/centered the player appears in frame, targeting the player occupying ~45% of frame area, clamped to 1.0–3.0× scale) before plotting onto a fixed 640×360 canvas; `ServeAnalysis` skips this normalization and just plots raw global pixel coordinates onto a canvas sized to that clip's native resolution — meaning **the two classes' swing-path images are not directly comparable in scale/normalization approach**, and a `ServeAnalysis` player/pro pair could differ in canvas size if the source clips differ in resolution (reflected in the `player_frame_w`/`pro_frame_w` fields returned specifically for that reason).
- **`ServeAnalysis` only** — **toss tracking**: runs the ball tracker YOLO model (`tracker.pt`, same weights as `Tracker` but invoked directly via `.predict()` rather than through `Tracker`/ByteTrack — no temporal tracking ID, just per-frame detection) every frame, keeping the highest-confidence detection, and buffers `(frame_idx, x, y)`. After the loop, `calculate_toss_stats`:
  1. Removes likely static false positives — bins detections into a coarse 10px grid and drops any detection whose grid cell recurs ≥8 times (a real toss moves; a static misdetection — e.g. a logo or light fixture the model consistently confuses for a ball — sits in the same bin repeatedly).
  2. Splits the remaining detections into contiguous "tracks" (`_split_into_tracks`) by walking them in frame order and starting a new track whenever there's either a >5-frame gap or a >80px jump from the previous point — a crude single-pass tracker substituting for not having ByteTrack's persistent IDs here.
  3. Keeps only tracks with at least 2 points and at least 20px of vertical range (filters out track fragments too short to represent an actual toss arc), then picks the **longest** surviving track as "the toss."
  4. Toss drift = horizontal pixel distance between the toss's first point (release) and its highest point (apex — `argmin(y)` since image y grows downward), converted to feet via `meters_per_pixel`.
  5. Returns `{"toss_drift_ft": None}` at any point the above can't produce a confident answer (too few detections, no candidate track, etc.) — see [Gaps](#gaps-you-will-need-to-work-around) regarding `null` handling.

**`run_analysis(player_video_path, pro_video_path)`** — shared logic: calls `analyze_shot` on both paths, then:
- Diffs average/peak velocity (`player − pro`).
- For each joint angle name present in both results, reports player average, pro average, their difference, and each side's `(min, max)` range across the clip.
- (`ServeAnalysis` only) diffs toss drift, `null`-safe via a small `_safe_diff` helper.
- Writes both swing-path images to `heatmaps/<uuid>_player.png` / `_pro.png` (a fresh UUID per request — old heatmaps are never cleaned up).
- Returns the combined dict described in [API contract](#api-contract) above.

### Models and assets

All under `sports/tennis/models/`, loaded by hardcoded relative path (hence the requirement to run everything from repo root):

| file | used by | purpose |
|---|---|---|
| `yolo11n.pt` | `ShotClassifier`, `GroundStrokeAnalysis`, `ServeAnalysis` | stock Ultralytics YOLO11-nano, COCO-pretrained — only the `person` class (id 0) is used, for locating the player in frame |
| `tracker.pt` | `Tracker`, `ServeAnalysis` | custom-trained YOLO model for detecting the tennis ball specifically |
| `pose_landmarker_full.task` | `ShotClassifier`, `GroundStrokeAnalysis`, `ServeAnalysis` | MediaPipe's "full" pose landmarker bundle (33 body landmarks) |
| `binary_shot_classifier.keras` | `ShotClassifier` | the trained forehand/backhand sequence model described above; deserialized with the custom `sc_model.ShotClassifier` class via `custom_objects` |
| `shot_classifier.keras` | not loaded anywhere in the live pipeline | earlier/alternate model artifact, kept for reference |
| `optimized_tracker.yaml` (in `ball_tracking/`, not `models/`) | `Tracker` | hand-tuned ByteTrack parameters |

`scripts/yolo_to_coreml.py` exports `tracker.pt` to CoreML with int8 quantization and embedded NMS — intended for running the ball tracker on Apple's Neural Engine (e.g. an iOS client) rather than via PyTorch/MPS as the current backend does. This is a separate, currently-unused export path, not part of the live request flow.

`sports/tennis/models/.cache/huggingface/` contains a cached CoreML `DepthAnythingV2Small` model download — not referenced by any code in this repo currently; appears to be leftover from exploratory work.

### Known limitations and gotchas

Consolidated from the analysis above — worth knowing before extending either the backend or a frontend on top of it:

- **30fps hardcoded** in `ContactDetector.get_audio_at_frame` and `ShotClassifier.classify_shots`'s MediaPipe timestamp calculation, regardless of the source video's actual frame rate (which `app.py` *does* read via `cap.get(cv.CAP_PROP_FPS)` but never passes down to either class). A video at a different frame rate will get subtly wrong audio-window alignment and pose timestamps.
- **1.7018m average player height** is assumed globally for all pixel→real-world conversions (net clearance, wrist velocity, toss drift) in three separate places (`NetClearance`, `GroundStrokeAnalysis`, `ServeAnalysis`) — not calibrated per-player, so absolute distance/speed numbers should be treated as rough estimates, though *relative* player-vs-pro comparisons partially cancel this out if both clips are filmed at similar camera distance/angle.
- The `frame_idx % 1 == 0` conditions in `ShotClassifier.classify_shots` (both the YOLO-detection and pose-landmarking cache branches) always evaluate `True`, so the "throttle every N frames" behavior implied by the surrounding variable names (`prev_pose`, `prev_landmarks_mp_result`) doesn't currently trigger — every frame does full YOLO + MediaPipe inference. If frame-throttling was intended (e.g. `% 3`), this is effectively disabled.
- **Net detection** (`NetClearance.locate_net`) assumes the net is horizontally-ish oriented and vertically centered in a fixed 160px-tall band of a 360px-tall frame — only valid for a specific camera framing (roughly baseline-level, net-centered shots). Different camera angles will likely fail to detect the net at all (`self.net` stays `None`, clearance never accumulates, and `get_final_clearance` divides by zero — see [Gaps](#gaps-you-will-need-to-work-around) #6).
- **Videos are R2-backed now, heatmaps are not.** `/process-tennis-video`'s output video and `/process-tennis-shot-analysis`'s input video are uploaded to R2 and their object `key` is returned in the response (see [Cloudflare R2 video storage](#cloudflare-r2-video-storage)) — `api/runs/` is now just a transient scratch directory, cleaned up after each request. `heatmaps/` still has no upload step and no static-file route, so a frontend still cannot display swing-path PNGs (see [Gaps](#gaps-you-will-need-to-work-around) #1).
- **Disk growth, unbounded** — now in R2 rather than local disk for videos: nothing ever deletes old R2 objects (every upload gets a fresh UUID key, no lifecycle policy referenced in this repo), and `heatmaps/` still grows locally forever, same as before. Long-running deployments will accumulate storage indefinitely either way.
- **Per-request model reloading**: `app.py` instantiates all analysis classes fresh on every request, which means every request reloads YOLO weights, the Keras model, and MediaPipe from disk from scratch — a meaningful fixed cost on top of actual video processing time. Moving model loading to module/app-level singletons (careful with MediaPipe's video-mode landmarker, which is stateful/timestamp-order-sensitive and isn't safely shareable across concurrent requests without a lock or a pool) would speed up repeated requests.
