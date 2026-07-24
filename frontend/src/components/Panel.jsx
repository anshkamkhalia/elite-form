// Bordered content section with an optional uppercase header and right-aligned
// header action. The core layout primitive for both the control rail and the
// results canvas.
export default function Panel({ title, action, children, bodyClass }) {
  return (
    <section className="panel">
      {(title || action) && (
        <div className="panel-head">
          <span className="panel-title">{title}</span>
          {action}
        </div>
      )}
      <div className={bodyClass ?? "panel-body"}>{children}</div>
    </section>
  );
}
