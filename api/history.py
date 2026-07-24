import json
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session

from api.auth import login_required
from api.db import get_db
from api.r2 import presigned_video_url
from api import typesense_index as ts

history_bp = Blueprint("history", __name__)


def save_analysis(
    user_id: int,
    kind: str,
    payload: dict,
    *,
    original_filename: str | None = None,
    video_key: str | None = None,
    shot_type: str | None = None,
    comparison_pro: str | None = None,
    summary: dict | None = None,
) -> int:
    """Persist one analysis for a user and return its new id."""
    db = get_db()
    created_at = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        """
        INSERT INTO analyses
            (user_id, kind, created_at, original_filename, video_key,
             shot_type, comparison_pro, summary_json, results_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            kind,
            created_at,
            original_filename,
            video_key,
            shot_type,
            comparison_pro,
            json.dumps(summary) if summary is not None else None,
            json.dumps(payload),
        ),
    )
    db.commit()
    analysis_id = cur.lastrowid

    # Keep the search index in sync (no-op if Typesense is offline).
    ts.upsert_analysis(
        {
            "id": analysis_id,
            "user_id": user_id,
            "kind": kind,
            "created_at": created_at,
            "original_filename": original_filename,
            "shot_type": shot_type,
            "comparison_pro": comparison_pro,
            "summary_json": summary,
        }
    )
    return analysis_id


@history_bp.route("/history", methods=["GET"])
@login_required
def list_history():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, kind, created_at, original_filename, shot_type,
               comparison_pro, summary_json
        FROM analyses
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (session["user_id"],),
    ).fetchall()

    items = [
        {
            "id": r["id"],
            "kind": r["kind"],
            "created_at": r["created_at"],
            "original_filename": r["original_filename"],
            "shot_type": r["shot_type"],
            "comparison_pro": r["comparison_pro"],
            "summary": json.loads(r["summary_json"]) if r["summary_json"] else None,
            "title": ts.analysis_title(r["kind"], r["shot_type"], r["comparison_pro"]),
        }
        for r in rows
    ]
    return jsonify({"items": items}), 200


@history_bp.route("/history/search", methods=["GET"])
@login_required
def search_history():
    q = (request.args.get("q") or "").strip()
    mode = (request.args.get("mode") or "all").strip().lower()
    kind = (request.args.get("kind") or "").strip().lower() or None
    shot_type = (request.args.get("shot_type") or "").strip().lower() or None
    pro = (request.args.get("pro") or "").strip() or None

    if mode not in ts.SEARCH_MODES:
        return (
            jsonify(
                {
                    "error": f"Invalid mode. Choose one of: {', '.join(ts.SEARCH_MODES)}",
                }
            ),
            400,
        )

    result = ts.search_analyses(
        session["user_id"],
        q=q,
        mode=mode,
        kind=kind,
        shot_type=shot_type,
        comparison_pro=pro,
    )
    if result is None:
        result = ts.search_sqlite(
            get_db(),
            session["user_id"],
            q=q,
            mode=mode,
            kind=kind,
            shot_type=shot_type,
            comparison_pro=pro,
        )

    return jsonify(result), 200
@history_bp.route("/history/search-modes", methods=["GET"])
@login_required
def search_modes():
    return (
        jsonify(
            {
                "modes": [
                    {
                        "id": mid,
                        "label": meta["label"],
                        "placeholder": meta["placeholder"],
                    }
                    for mid, meta in ts.SEARCH_MODES.items()
                ],
                "engine": "typesense" if ts.is_available() else "sqlite",
                "typesense_configured": ts.is_configured(),
            }
        ),
        200,
    )
@history_bp.route("/history/<int:analysis_id>", methods=["GET"])
@login_required
def get_history(analysis_id: int):
    db = get_db()
    row = db.execute(
        "SELECT * FROM analyses WHERE id = ? AND user_id = ?",
        (analysis_id, session["user_id"]),
    ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404

    payload = json.loads(row["results_json"])
    # Presigned URLs expire (24h); mint a fresh one from the stored key.
    if row["video_key"]:
        try:
            payload["video_url"] = presigned_video_url(row["video_key"])
        except Exception:
            pass

    return (
        jsonify(
            {
                "id": row["id"],
                "kind": row["kind"],
                "created_at": row["created_at"],
                "original_filename": row["original_filename"],
                "shot_type": row["shot_type"],
                "comparison_pro": row["comparison_pro"],
                "payload": payload,
            }
        ),
        200,
    )
@history_bp.route("/history/<int:analysis_id>", methods=["DELETE"])
@login_required
def delete_history(analysis_id: int):
    db = get_db()
    cur = db.execute(
        "DELETE FROM analyses WHERE id = ? AND user_id = ?",
        (analysis_id, session["user_id"]),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    ts.delete_analysis(analysis_id)
    return jsonify({"ok": True}), 200
