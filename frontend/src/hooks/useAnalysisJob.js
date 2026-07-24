import { useRef, useState } from "react";

// Shared workflow state for both analysis flows: holds the selected file and
// the phase machine (idle → running → done/error), and wraps a backend call
// with cancellation. Keeps the two flow components focused on their own UI.
export function useAnalysisJob() {
  const [file, setFile] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | running | done
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  const run = async (call) => {
    if (!file) return;
    setError(null);
    setResult(null);
    setPhase("running");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const data = await call(file, controller.signal);
      setResult(data);
      setPhase("done");
    } catch (err) {
      if (err.name === "AbortError") setPhase("idle");
      else {
        setError(err.message);
        setPhase("idle");
      }
    }
  };

  const cancel = () => abortRef.current?.abort();

  const reset = () => {
    setFile(null);
    setResult(null);
    setError(null);
    setPhase("idle");
  };

  return { file, setFile, phase, result, error, run, cancel, reset };
}
