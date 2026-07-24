import { useEffect, useState } from "react";
import { fetchProClips, processShotAnalysis } from "../api";
import { SHOT_TYPES } from "../config";
import { useAnalysisJob } from "../hooks/useAnalysisJob";
import Panel from "../components/Panel";
import FileDrop from "../components/FileDrop";
import AnalysisStatus from "../components/AnalysisStatus";
import ComparisonResults from "../components/ComparisonResults";
import { IconAlert, IconChart } from "../components/icons";

export default function ShotComparisonFlow() {
  const job = useAnalysisJob();
  const { file, setFile, phase, result, error } = job;

  const [shotType, setShotType] = useState("forehand");
  const [pro, setPro] = useState("");
  const [proOptions, setProOptions] = useState(null);
  const [proOptionsError, setProOptionsError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchProClips()
      .then((data) => {
        if (cancelled) return;
        setProOptions(data);
        setPro(data[shotType]?.[0] || "");
      })
      .catch((err) => {
        if (!cancelled) setProOptionsError(err.message);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pickShotType = (t) => {
    setShotType(t);
    setPro(proOptions?.[t]?.[0] || "");
  };

  return (
    <div className="workspace">
      <aside className="rail">
        <Panel title="Source">
          <FileDrop
            file={file}
            onFile={setFile}
            hint="One shot per clip · native resolution is preserved"
          />
        </Panel>

        <Panel title="Options">
          <div className="field">
            <label>Shot type</label>
            <div className="seg" role="group" aria-label="Shot type">
              {SHOT_TYPES.map((t) => (
                <button
                  key={t}
                  type="button"
                  className={t === shotType ? "active" : ""}
                  onClick={() => pickShotType(t)}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
          <div className="field">
            <label>Compare against</label>
            <select
              value={pro}
              onChange={(e) => setPro(e.target.value)}
              disabled={!proOptions}
            >
              {!proOptions && <option>Loading professional clips…</option>}
              {proOptions?.[shotType]?.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
        </Panel>

        {proOptionsError && (
          <div className="alert alert-error" role="alert">
            <IconAlert size={16} />
            <div>Couldn't load professional clips: {proOptionsError}</div>
          </div>
        )}

        {error && (
          <div className="alert alert-error" role="alert">
            <IconAlert size={16} />
            <div>{error}</div>
          </div>
        )}

        <div className="rail-actions">
          <button
            type="button"
            className="btn btn-primary btn-block"
            disabled={!file || !pro || phase === "running"}
            onClick={() =>
              job.run((f, signal) =>
                processShotAnalysis(f, shotType, pro, signal)
              )
            }
          >
            Compare shot
          </button>
          {(file || result) && phase !== "running" && (
            <button
              type="button"
              className="btn btn-subtle btn-block"
              onClick={job.reset}
            >
              Reset
            </button>
          )}
        </div>
      </aside>

      <section className="canvas">
        {phase === "running" && (
          <AnalysisStatus
            title={`Comparing ${shotType} against ${pro}`}
            message="Running pose estimation over your clip and the pro's reference clip, then diffing wrist velocity and six joint angles."
            onCancel={job.cancel}
          />
        )}

        {phase === "idle" && (
          <div className="empty">
            <div className="e-icon">
              <IconChart />
            </div>
            <h3>No comparison yet</h3>
            <p>
              Upload a single-shot clip, choose the shot type and a professional
              reference, then run the comparison to see velocity, joint angles,
              and swing path side by side.
            </p>
          </div>
        )}

        {phase === "done" && result && (
          <ComparisonResults
            result={result}
            shotType={shotType}
            pro={pro}
            actionLabel="New comparison"
            onAction={job.reset}
          />
        )}
      </section>
    </div>
  );
}
