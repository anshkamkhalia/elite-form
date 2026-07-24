import Panel from "./Panel";
import VideoResult from "./VideoResult";
import { IconRuler, IconPulse } from "./icons";

function fmt(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toFixed(digits);
}

// Renders the aggregate stats for a full-session analysis. Used by the live
// Session Analysis flow and replayed from saved history.
export default function SessionResults({
  result,
  title = "Session results",
  subtitle = "Aggregate statistics across the full clip",
  actionLabel,
  onAction,
}) {
  return (
    <div className="stack">
      <div className="results-head">
        <div>
          <h2>{title}</h2>
          <div className="sub">{subtitle}</div>
        </div>
        {actionLabel && onAction && (
          <button type="button" className="btn btn-secondary" onClick={onAction}>
            {actionLabel}
          </button>
        )}
      </div>

      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-label">
            <IconRuler size={13} /> Net clearance
          </div>
          <div className="stat-value">
            {fmt(result.net_clearance)}
            <span className="stat-unit">in avg</span>
          </div>
          <div className="stat-foot">Average ball height above the net</div>
        </div>

        <div className="stat-card">
          <div className="stat-label">
            <IconPulse size={13} /> Racket contacts
          </div>
          <div className="stat-value">{result.n_contacts ?? "—"}</div>
          <div className="stat-foot">Impacts detected in the audio track</div>
        </div>
      </div>

      <Panel title="Shot classification">
        <div className="split-bar">
          <div className="fh" style={{ width: `${result.fh_percent ?? 0}%` }} />
          <div className="bh" style={{ width: `${result.bh_percent ?? 0}%` }} />
        </div>
        <div className="split-legend">
          <span>
            <span className="swatch" style={{ background: "var(--accent)" }} />
            Forehand {fmt(result.fh_percent)}%
          </span>
          <span>
            <span className="swatch" style={{ background: "var(--blue)" }} />
            Backhand {fmt(result.bh_percent)}%
          </span>
        </div>
      </Panel>

      <Panel title="Annotated video">
        <VideoResult
          videoKey={result.key}
          videoUrl={result.video_url}
          label="The annotated output video (ball-tracking boxes and shot labels)"
        />
      </Panel>
    </div>
  );
}
