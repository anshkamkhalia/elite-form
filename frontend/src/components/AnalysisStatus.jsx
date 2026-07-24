import { useEffect, useState } from "react";

function fmt(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// Inline processing state shown in the results canvas. The backend is
// synchronous with no progress signal, so we show elapsed time and a cancel.
export default function AnalysisStatus({ title, message, onCancel }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="status">
      <div className="spinner" aria-hidden="true" />
      <h3>{title}</h3>
      <div className="elapsed" aria-live="polite">
        {fmt(elapsed)}
      </div>
      <p>{message}</p>
      <button type="button" className="btn btn-secondary" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}
