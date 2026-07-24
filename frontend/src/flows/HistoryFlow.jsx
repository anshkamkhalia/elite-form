import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchHistory,
  fetchHistoryItem,
  deleteHistoryItem,
  searchHistory,
  fetchHistorySearchModes,
} from "../api";
import Panel from "../components/Panel";
import SessionResults from "../components/SessionResults";
import ComparisonResults from "../components/ComparisonResults";
import {
  IconAlert,
  IconHistory,
  IconFilm,
  IconCompare,
  IconTrash,
} from "../components/icons";

const DEFAULT_MODES = [
  { id: "all", label: "Everything", placeholder: "Search filename, pro, shot type…" },
  { id: "filename", label: "Filename", placeholder: "e.g. test_forehand.mp4" },
  { id: "pro", label: "Pro / player", placeholder: "e.g. Dimitrov, Sinner" },
  { id: "shot_type", label: "Shot type", placeholder: "forehand, backhand, or serve" },
  { id: "kind", label: "Analysis type", placeholder: "session or comparison" },
];

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function itemTitle(item) {
  if (item.title) return item.title;
  if (item.kind === "comparison") {
    const shot = item.shot_type
      ? item.shot_type.charAt(0).toUpperCase() + item.shot_type.slice(1)
      : "Shot";
    return `${shot} vs. ${item.comparison_pro ?? "pro"}`;
  }
  return "Session analysis";
}

function itemSubtitle(item) {
  const parts = [];
  if (item.original_filename) parts.push(item.original_filename);
  parts.push(formatDate(item.created_at));
  return parts.filter(Boolean).join(" · ");
}

export default function HistoryFlow() {
  const [items, setItems] = useState(null);
  const [listError, setListError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const [modes, setModes] = useState(DEFAULT_MODES);
  const [mode, setMode] = useState("all");
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState(""); // "" | session | comparison
  const [shotFilter, setShotFilter] = useState(""); // "" | forehand | backhand | serve
  const [engine, setEngine] = useState(null);
  const [searching, setSearching] = useState(false);
  const [found, setFound] = useState(null);
  const debounceRef = useRef(null);
  const searchAbortRef = useRef(null);

  const activeMode = modes.find((m) => m.id === mode) || DEFAULT_MODES[0];
  const hasActiveSearch =
    Boolean(query.trim()) || Boolean(kindFilter) || Boolean(shotFilter);

  const loadAll = useCallback(async () => {
    setListError(null);
    setSearching(false);
    setFound(null);
    try {
      const list = await fetchHistory();
      setItems(list);
    } catch (err) {
      setListError(err.message || "Could not load history.");
      setItems([]);
    }
  }, []);

  const runSearch = useCallback(
    async (opts) => {
      const {
        q = query,
        m = mode,
        kind = kindFilter,
        shot = shotFilter,
      } = opts || {};

      const active =
        Boolean((q || "").trim()) || Boolean(kind) || Boolean(shot);

      if (!active) {
        return loadAll();
      }

      searchAbortRef.current?.abort();
      const controller = new AbortController();
      searchAbortRef.current = controller;
      setSearching(true);
      setListError(null);
      try {
        const data = await searchHistory(
          {
            q: (q || "").trim(),
            mode: m,
            kind: kind || undefined,
            shotType: shot || undefined,
          },
          controller.signal
        );
        setItems(data.items || []);
        setFound(data.found ?? data.items?.length ?? 0);
        setEngine(data.engine || null);
      } catch (err) {
        if (err.name === "AbortError") return;
        setListError(err.message || "Search failed.");
        setItems([]);
      } finally {
        setSearching(false);
      }
    },
    [query, mode, kindFilter, shotFilter, loadAll]
  );

  useEffect(() => {
    loadAll();
    fetchHistorySearchModes()
      .then((data) => {
        if (data?.modes?.length) setModes(data.modes);
        if (data?.engine) setEngine(data.engine);
      })
      .catch(() => {
        /* keep defaults */
      });
  }, [loadAll]);

  // Debounced search when query / mode / filters change
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      runSearch();
    }, 280);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, mode, kindFilter, shotFilter, runSearch]);

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    setLoadingDetail(true);
    setDetailError(null);
    setDetail(null);
    fetchHistoryItem(selectedId, controller.signal)
      .then(setDetail)
      .catch((err) => {
        if (err.name !== "AbortError") setDetailError(err.message);
      })
      .finally(() => setLoadingDetail(false));
    return () => controller.abort();
  }, [selectedId]);

  const remove = async (id, e) => {
    e.stopPropagation();
    if (!window.confirm("Delete this saved analysis? This cannot be undone.")) {
      return;
    }
    try {
      await deleteHistoryItem(id);
      if (selectedId === id) setSelectedId(null);
      setItems((prev) => (prev ? prev.filter((it) => it.id !== id) : prev));
      if (found != null) setFound((n) => Math.max(0, (n ?? 1) - 1));
    } catch (err) {
      setListError(err.message || "Could not delete that item.");
    }
  };

  const clearSearch = () => {
    setQuery("");
    setKindFilter("");
    setShotFilter("");
    setMode("all");
  };

  return (
    <div className="workspace">
      <aside className="rail">
        <Panel title="Find analysis">
          <div className="field">
            <label htmlFor="hist-mode">Search by</label>
            <select
              id="hist-mode"
              value={mode}
              onChange={(e) => setMode(e.target.value)}
            >
              {modes.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="hist-q">Query</label>
            <input
              id="hist-q"
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={activeMode.placeholder}
              autoComplete="off"
            />
          </div>

          <div className="field">
            <label>Quick filters</label>
            <div className="hist-filters" role="group" aria-label="Analysis type">
              {[
                { id: "", label: "All types" },
                { id: "session", label: "Session" },
                { id: "comparison", label: "Comparison" },
              ].map((f) => (
                <button
                  key={f.id || "all-kind"}
                  type="button"
                  className={kindFilter === f.id ? "active" : ""}
                  onClick={() => setKindFilter(f.id)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <div
              className="hist-filters"
              role="group"
              aria-label="Shot type"
              style={{ marginTop: 6 }}
            >
              {[
                { id: "", label: "All shots" },
                { id: "forehand", label: "FH" },
                { id: "backhand", label: "BH" },
                { id: "serve", label: "Serve" },
              ].map((f) => (
                <button
                  key={f.id || "all-shot"}
                  type="button"
                  className={shotFilter === f.id ? "active" : ""}
                  onClick={() => setShotFilter(f.id)}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {hasActiveSearch && (
            <div className="hist-search-meta">
              <span>
                {searching
                  ? "Searching…"
                  : found != null
                  ? `${found} result${found === 1 ? "" : "s"}`
                  : ""}
              </span>
              <button type="button" className="link" onClick={clearSearch}>
                Clear
              </button>
            </div>
          )}
          {engine && (
            <div className="hist-engine" title="Search backend in use">
              via {engine}
            </div>
          )}
        </Panel>

        <Panel title="Saved analyses">
          {items === null && !listError ? (
            <div className="coach-loading">
              <span className="spinner spinner-sm" aria-hidden="true" />
              Loading…
            </div>
          ) : listError ? (
            <div className="alert alert-error" role="alert">
              <IconAlert size={16} />
              <div>{listError}</div>
            </div>
          ) : items.length === 0 ? (
            <div className="hist-empty-rail">
              {hasActiveSearch
                ? "No analyses match this search. Try a different mode or clear filters."
                : "Nothing saved yet. Run a Session Analysis or Shot Comparison and it will appear here."}
            </div>
          ) : (
            <ul className="hist-list">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`hist-item ${selectedId === item.id ? "active" : ""}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <span className="hist-icon">
                      {item.kind === "comparison" ? (
                        <IconCompare size={16} />
                      ) : (
                        <IconFilm size={16} />
                      )}
                    </span>
                    <span className="hist-meta">
                      <span className="hist-title">{itemTitle(item)}</span>
                      <span className="hist-date">{itemSubtitle(item)}</span>
                    </span>
                    <span
                      className="hist-del"
                      role="button"
                      tabIndex={0}
                      aria-label="Delete"
                      title="Delete"
                      onClick={(e) => remove(item.id, e)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") remove(item.id, e);
                      }}
                    >
                      <IconTrash size={14} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </aside>

      <section className="canvas">
        {selectedId == null ? (
          <div className="empty">
            <div className="e-icon">
              <IconHistory />
            </div>
            <h3>Your analysis history</h3>
            <p>
              Search by filename, pro, shot type, or analysis type to find a
              specific clip among dozens. Select a result to review stats, swing
              paths, and video again.
            </p>
          </div>
        ) : loadingDetail ? (
          <div className="empty">
            <div className="coach-loading">
              <span className="spinner spinner-sm" aria-hidden="true" />
              Loading saved analysis…
            </div>
          </div>
        ) : detailError ? (
          <div className="alert alert-error" role="alert">
            <IconAlert size={16} />
            <div>{detailError}</div>
          </div>
        ) : detail ? (
          detail.kind === "comparison" ? (
            <ComparisonResults
              result={detail.payload}
              shotType={detail.shot_type || "shot"}
              pro={detail.comparison_pro || "pro"}
              subtitle={`Saved ${formatDate(detail.created_at)} · differences are your value minus the pro`}
            />
          ) : (
            <SessionResults
              result={detail.payload}
              subtitle={`Saved ${formatDate(detail.created_at)} · aggregate statistics across the full clip`}
            />
          )
        ) : null}
      </section>
    </div>
  );
}
