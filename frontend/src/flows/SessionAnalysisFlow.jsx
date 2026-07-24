import { processTennisVideo } from "../api";
import { useAnalysisJob } from "../hooks/useAnalysisJob";
import Panel from "../components/Panel";
import FileDrop from "../components/FileDrop";
import AnalysisStatus from "../components/AnalysisStatus";
import SessionResults from "../components/SessionResults";
import { IconAlert, IconChart } from "../components/icons";

export default function SessionAnalysisFlow() {
  const job = useAnalysisJob();
  const { file, setFile, phase, result, error } = job;

  return (
    <div className="workspace">
      <aside className="rail">
        <Panel title="Source">
          <FileDrop
            file={file}
            onFile={setFile}
            hint="Rally or practice clip · baseline-level, net-centered framing works best"
          />
        </Panel>

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
            disabled={!file || phase === "running"}
            onClick={() => job.run(processTennisVideo)}
          >
            Analyze session
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
            title="Analyzing session"
            message="Running ball tracking, pose estimation, shot classification, audio contact detection, and net-clearance measurement on every frame. Expect several seconds of processing per second of footage."
            onCancel={job.cancel}
          />
        )}

        {phase === "idle" && (
          <div className="empty">
            <div className="e-icon">
              <IconChart />
            </div>
            <h3>No session analyzed yet</h3>
            <p>
              Upload a practice clip and run the analysis. Shot classification,
              net clearance, contact detection, and the annotated video will
              appear here.
            </p>
          </div>
        )}

        {phase === "done" && result && (
          <SessionResults
            result={result}
            actionLabel="New analysis"
            onAction={job.reset}
          />
        )}
      </section>
    </div>
  );
}
