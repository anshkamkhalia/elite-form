import { useEffect, useState } from "react";
import { fetchCoachingTips } from "../api";
import { heatmapUrlFromPath } from "../config";
import Panel from "./Panel";
import VideoResult from "./VideoResult";
import CompareBar from "./CompareBar";

function fmt(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toFixed(digits);
}

function fmtRange(range) {
  if (!Array.isArray(range) || range.length < 2) return "—";
  return `${fmt(range[0], 0)}–${fmt(range[1], 0)}°`;
}

function DiffPill({ value, unit = "°" }) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="pill zero">n/a</span>;
  }
  const cls = value > 0 ? "pos" : value < 0 ? "neg" : "zero";
  const sign = value > 0 ? "+" : "";
  return (
    <span className={`pill ${cls}`}>
      {sign}
      {fmt(value)}
      {unit}
    </span>
  );
}

// Renders a full shot-comparison result. Used by the live Shot Comparison flow
// and replayed from saved history.
export default function ComparisonResults({
  result,
  shotType,
  pro,
  title,
  subtitle = "Differences are your value minus the pro",
  actionLabel,
  onAction,
}) {
  const r = result.results ?? {};
  const velocity = r.velocity ?? {};
  const angles = r.joint_angles ?? {};
  const toss = r.toss;
  const isServe = toss !== undefined;

  const heading =
    title ??
    `${shotType.charAt(0).toUpperCase() + shotType.slice(1)} vs. ${pro}`;

  let flagged = null;
  let maxAbs = -1;
  for (const [joint, a] of Object.entries(angles)) {
    const d = Math.abs(a?.difference ?? NaN);
    if (!Number.isNaN(d) && d > maxAbs) {
      maxAbs = d;
      flagged = joint;
    }
  }

  return (
    <div className="stack">
      <div className="results-head">
        <div>
          <h2>{heading}</h2>
          <div className="sub">{subtitle}</div>
        </div>
        {actionLabel && onAction && (
          <button type="button" className="btn btn-secondary" onClick={onAction}>
            {actionLabel}
          </button>
        )}
      </div>

      <Panel title="Wrist velocity">
        <div className="compare-grid">
          <CompareBar
            name="Average"
            player={velocity.player?.average}
            pro={velocity.pro?.average}
            difference={velocity.average_difference}
            unit=" mph"
          />
          <CompareBar
            name="Peak"
            player={velocity.player?.peak}
            pro={velocity.pro?.peak}
            difference={velocity.peak_difference}
            unit=" mph"
          />
        </div>
      </Panel>

      <Panel title="Joint angles">
        <div className="table-wrap">
          <table className="angles">
            <thead>
              <tr>
                <th>Joint</th>
                <th>You</th>
                <th>{pro}</th>
                <th>Difference</th>
                <th>Your range</th>
                <th>Pro range</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(angles).map(([joint, a]) => (
                <tr key={joint} className={joint === flagged ? "flag" : ""}>
                  <td className="joint">
                    {joint.replace(/_/g, " ")}
                    {joint === flagged && (
                      <span className="flag-tag">Largest gap</span>
                    )}
                  </td>
                  <td>{fmt(a.player_average)}°</td>
                  <td>{fmt(a.pro_average)}°</td>
                  <td>
                    <DiffPill value={a.difference} />
                  </td>
                  <td className="range">{fmtRange(a.player_range)}</td>
                  <td className="range">{fmtRange(a.pro_range)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      {isServe && (
        <Panel title="Serve toss drift">
          {toss?.player?.toss_drift_ft == null &&
          toss?.pro?.toss_drift_ft == null ? (
            <div className="toss-null">
              The ball tracker couldn&apos;t find a usable toss trajectory in
              one or both clips, so toss drift isn&apos;t available.
            </div>
          ) : (
            <CompareBar
              name="Release → apex drift"
              player={toss?.player?.toss_drift_ft}
              pro={toss?.pro?.toss_drift_ft}
              difference={toss?.drift_difference_ft}
              unit=" ft"
            />
          )}
        </Panel>
      )}

      <Panel title="Swing path">
        <SwingPath visuals={r.visuals} />
      </Panel>

      <Panel title="Coach's notes">
        <CoachingTips results={r} shotType={shotType} pro={pro} />
      </Panel>

      <Panel title="Your uploaded clip">
        <VideoResult
          videoKey={result.key}
          videoUrl={result.video_url}
          label="Your uploaded clip"
        />
      </Panel>
    </div>
  );
}

// Side-by-side swing-path heatmaps. The backend writes the PNGs into
// frontend/heatmaps/ and the dev server serves them at /heatmaps/<file>.
function SwingPath({ visuals }) {
  return (
    <div className="swing-grid">
      {[
        { cap: "You", path: visuals?.player_swing_path },
        { cap: "Pro", path: visuals?.pro_swing_path },
      ].map(({ cap, path }) => {
        const url = heatmapUrlFromPath(path);
        return (
          <div className="swing-slot" key={cap}>
            <div className="cap">{cap}</div>
            {url ? (
              <img src={url} alt={`${cap} swing path heatmap`} />
            ) : (
              <div className="ph">No swing path available</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// Conversational coaching feedback generated by the backend's AI endpoint
// from the same metrics shown above.
function CoachingTips({ results, shotType, pro }) {
  const [coaching, setCoaching] = useState(null);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setCoaching(null);
    setError(null);
    fetchCoachingTips(results, shotType, pro, controller.signal)
      .then(setCoaching)
      .catch((err) => {
        if (err.name !== "AbortError") setError(err.message);
      });
    return () => controller.abort();
  }, [results, shotType, pro, attempt]);

  if (error) {
    return (
      <div>
        <div className="toss-null" style={{ marginBottom: 12 }}>
          {error}
        </div>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => setAttempt((a) => a + 1)}
        >
          Retry
        </button>
      </div>
    );
  }
  if (!coaching) {
    return (
      <div className="coach-loading">
        <span className="spinner spinner-sm" aria-hidden="true" />
        Writing feedback from your metrics…
      </div>
    );
  }
  return (
    <div className="coach-tips">
      {coaching.tldr && (
        <div className="coach-tldr">
          <span className="coach-tldr-label">TL;DR</span>
          <p>{coaching.tldr}</p>
        </div>
      )}
      {coaching.tips.split(/\n{2,}/).map((p, i) => (
        <p key={i}>{p}</p>
      ))}
    </div>
  );
}
