# Atlas Motion — Frontend

React (Vite) frontend for the Atlas Motion tennis-analysis backend. Two flows,
mapping 1:1 to the backend's two endpoints:

- **Full Video Analysis** → `POST /process-tennis-video` — net clearance,
  contact count, forehand/backhand mix, annotated output video.
- **Shot Comparison** → `POST /process-tennis-shot-analysis` — wrist velocity,
  six joint angles, and (for serves) toss drift, compared against a pro clip
  (Dimitrov / Sinner / Alcaraz).

## Running

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
```

Start the Flask backend separately from the repo root (`bash api/run_api.sh`,
serves on port 5001). The backend has no CORS headers, so the Vite dev server
proxies `/api/*` to it — no backend changes needed.

## Configuration

Copy `.env.example` to `.env`:

- `VITE_BACKEND_URL` — where the proxy forwards requests (default
  `http://localhost:5001`).
- `VITE_R2_PUBLIC_BASE_URL` — public base URL of the Cloudflare R2 bucket.
  Responses return R2 object keys; the app joins this base URL with the key to
  play videos. Without it, stats still display and keys are shown/copyable,
  but videos can't be streamed.

## Backend quirks handled

- Requests block until processing finishes (no job queue): indeterminate
  spinner with elapsed timer, no client-side timeout, user-initiated cancel.
- Backend errors surface as HTML 500 pages: any non-200/non-JSON response is
  shown as a friendly "try a clearer clip" error.
- `toss_drift_ft` can be `null`: rendered with an explanation instead of a bar.
- Swing-path heatmap PNGs aren't served by the backend (filesystem paths
  only): a note explains why they aren't displayed.
- Pro-clip options aren't discoverable via the API: hardcoded per the README
  (forehand → Dimitrov, backhand → Sinner, serve → Alcaraz).

## Mock backend

To exercise the UI without models/GPU:

```bash
npm run mock       # fake Flask on port 5001 with realistic responses
```
