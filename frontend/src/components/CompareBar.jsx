function fmtNum(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toFixed(digits);
}

function DeltaPill({ value, unit = "", digits = 1 }) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="pill zero">n/a</span>;
  }
  const cls = value > 0 ? "pos" : value < 0 ? "neg" : "zero";
  const sign = value > 0 ? "+" : "";
  return (
    <span className={`pill ${cls}`}>
      {sign}
      {fmtNum(value, digits)}
      {unit}
    </span>
  );
}

// Side-by-side player-vs-pro bars for one metric, scaled against the larger
// of the two values so the gap reads visually.
export default function CompareBar({
  name,
  player,
  pro,
  difference,
  unit = "",
  digits = 1,
}) {
  const max = Math.max(Math.abs(player ?? 0), Math.abs(pro ?? 0), 1e-6);
  const w = (v) => `${Math.max((Math.abs(v ?? 0) / max) * 100, 1.5)}%`;

  return (
    <div className="vs-row">
      <div className="vs-head">
        <span className="vs-name">{name}</span>
        <DeltaPill value={difference} unit={unit} digits={digits} />
      </div>
      <div className="vs-bars">
        <div className="vs-bar-row">
          <span className="vs-bar-who">You</span>
          <div className="vs-bar-track">
            <div className="vs-bar-fill player" style={{ width: w(player) }} />
          </div>
          <span className="vs-bar-num">
            {fmtNum(player, digits)}
            {unit}
          </span>
        </div>
        <div className="vs-bar-row">
          <span className="vs-bar-who">Pro</span>
          <div className="vs-bar-track">
            <div className="vs-bar-fill pro" style={{ width: w(pro) }} />
          </div>
          <span className="vs-bar-num">
            {fmtNum(pro, digits)}
            {unit}
          </span>
        </div>
      </div>
    </div>
  );
}
