"""Typesense index for analysis history search.

SQLite remains the source of truth. Typesense is a search index synced on
save/delete. If Typesense is not configured or unreachable, search falls
back to SQLite so the History UI still works locally without Docker.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()

COLLECTION = "analyses"

# Search modes → Typesense query_by fields (and SQLite columns for fallback).
SEARCH_MODES = {
    "all": {
        "label": "Everything",
        "query_by": "title,original_filename,shot_type,comparison_pro,kind",
        "placeholder": "Search filename, pro, shot type…",
    },
    "filename": {
        "label": "Filename",
        "query_by": "original_filename",
        "placeholder": "e.g. test_forehand.mp4",
    },
    "pro": {
        "label": "Pro / player",
        "query_by": "comparison_pro",
        "placeholder": "e.g. Dimitrov, Sinner, Alcaraz",
    },
    "shot_type": {
        "label": "Shot type",
        "query_by": "shot_type",
        "placeholder": "forehand, backhand, or serve",
    },
    "kind": {
        "label": "Analysis type",
        "query_by": "kind",
        "placeholder": "session or comparison",
    },
}

SCHEMA = {
    "name": COLLECTION,
    "fields": [
        {"name": "id", "type": "string"},
        {"name": "user_id", "type": "int32", "facet": True},
        {"name": "kind", "type": "string", "facet": True},
        {"name": "created_at", "type": "int64"},
        {"name": "created_at_iso", "type": "string", "optional": True, "index": False},
        {"name": "original_filename", "type": "string", "optional": True},
        {"name": "title", "type": "string"},
        {"name": "shot_type", "type": "string", "facet": True, "optional": True},
        {"name": "comparison_pro", "type": "string", "facet": True, "optional": True},
        {"name": "net_clearance", "type": "float", "optional": True},
        {"name": "n_contacts", "type": "int32", "optional": True},
        {"name": "avg_velocity_diff", "type": "float", "optional": True},
    ],
    "default_sorting_field": "created_at",
}


def _client():
    """Return a Typesense Client, or None if not configured."""
    api_key = os.getenv("TYPESENSE_API_KEY", "").strip()
    host = os.getenv("TYPESENSE_HOST", "").strip()
    if not api_key or not host:
        return None
    try:
        import typesense
    except ImportError:
        return None

    port = int(os.getenv("TYPESENSE_PORT", "8108"))
    protocol = os.getenv("TYPESENSE_PROTOCOL", "http").strip() or "http"
    return typesense.Client(
        {
            "nodes": [{"host": host, "port": port, "protocol": protocol}],
            "api_key": api_key,
            "connection_timeout_seconds": 3,
        }
    )


def is_configured() -> bool:
    return bool(os.getenv("TYPESENSE_API_KEY", "").strip() and os.getenv("TYPESENSE_HOST", "").strip())


def is_available() -> bool:
    client = _client()
    if client is None:
        return False
    try:
        client.operations.is_healthy()
        return True
    except Exception:
        return False


def analysis_title(
    kind: str,
    shot_type: str | None = None,
    comparison_pro: str | None = None,
) -> str:
    if kind == "comparison":
        shot = (shot_type or "shot").capitalize()
        return f"{shot} vs. {comparison_pro or 'pro'}"
    return "Session analysis"


def _created_at_ts(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        # Handle both with and without timezone offset
        text = iso.replace("Z", "+00:00")
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


def _summary_dict(summary_json: str | dict | None) -> dict:
    if summary_json is None:
        return {}
    if isinstance(summary_json, dict):
        return summary_json
    try:
        return json.loads(summary_json)
    except (TypeError, json.JSONDecodeError):
        return {}


def document_from_row(row: Any) -> dict:
    """Build a Typesense document from a sqlite Row or mapping."""
    kind = row["kind"] if hasattr(row, "keys") else row.get("kind")
    shot_type = row["shot_type"] if "shot_type" in row.keys() else row.get("shot_type")
    comparison_pro = (
        row["comparison_pro"] if "comparison_pro" in row.keys() else row.get("comparison_pro")
    )
    created_at = row["created_at"] if "created_at" in row.keys() else row.get("created_at")
    summary = _summary_dict(
        row["summary_json"] if "summary_json" in row.keys() else row.get("summary_json")
    )
    analysis_id = row["id"] if "id" in row.keys() else row.get("id")
    user_id = row["user_id"] if "user_id" in row.keys() else row.get("user_id")
    filename = (
        row["original_filename"]
        if "original_filename" in row.keys()
        else row.get("original_filename")
    )

    doc: dict[str, Any] = {
        "id": str(analysis_id),
        "user_id": int(user_id),
        "kind": kind or "session",
        "created_at": _created_at_ts(created_at),
        "created_at_iso": created_at or "",
        "title": analysis_title(kind, shot_type, comparison_pro),
        "original_filename": filename or "",
    }
    if shot_type:
        doc["shot_type"] = shot_type
    if comparison_pro:
        doc["comparison_pro"] = comparison_pro
    if summary.get("net_clearance") is not None:
        try:
            doc["net_clearance"] = float(summary["net_clearance"])
        except (TypeError, ValueError):
            pass
    if summary.get("n_contacts") is not None:
        try:
            doc["n_contacts"] = int(summary["n_contacts"])
        except (TypeError, ValueError):
            pass
    if summary.get("avg_velocity_diff") is not None:
        try:
            doc["avg_velocity_diff"] = float(summary["avg_velocity_diff"])
        except (TypeError, ValueError):
            pass
    return doc


def ensure_collection() -> bool:
    """Create the analyses collection if missing. Returns False if unavailable."""
    client = _client()
    if client is None:
        return False
    try:
        client.collections[COLLECTION].retrieve()
        return True
    except Exception:
        pass
    try:
        client.collections.create(SCHEMA)
        return True
    except Exception as e:
        print(f"[typesense] ensure_collection failed: {e}")
        return False


def upsert_analysis(doc_or_row: Any) -> None:
    """Index (or update) one analysis. No-ops if Typesense is down."""
    client = _client()
    if client is None:
        return
    try:
        ensure_collection()
        doc = doc_or_row if isinstance(doc_or_row, dict) and "title" in doc_or_row else document_from_row(doc_or_row)
        client.collections[COLLECTION].documents.upsert(doc)
    except Exception as e:
        print(f"[typesense] upsert failed: {e}")


def delete_analysis(analysis_id: int | str) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.collections[COLLECTION].documents[str(analysis_id)].delete()
    except Exception as e:
        print(f"[typesense] delete failed: {e}")


def reindex_all(rows: list) -> int:
    """Replace collection contents with the given SQLite rows. Returns count."""
    client = _client()
    if client is None:
        raise RuntimeError(
            "Typesense is not configured. Set TYPESENSE_HOST and TYPESENSE_API_KEY in .env"
        )
    # Drop + recreate so schema stays in sync.
    try:
        client.collections[COLLECTION].delete()
    except Exception:
        pass
    client.collections.create(SCHEMA)
    docs = [document_from_row(r) for r in rows]
    if not docs:
        return 0
    # Import in one batch
    import_lines = "\n".join(json.dumps(d) for d in docs)
    client.collections[COLLECTION].documents.import_(
        import_lines, {"action": "upsert"}
    )
    return len(docs)


def search_analyses(
    user_id: int,
    *,
    q: str = "",
    mode: str = "all",
    kind: str | None = None,
    shot_type: str | None = None,
    comparison_pro: str | None = None,
    per_page: int = 50,
) -> dict | None:
    """Search Typesense for this user. Returns None if Typesense is unavailable.

    Result shape: ``{"items": [...], "engine": "typesense", "found": N}``
    where each item matches the /history list payload (without summary metrics
    beyond what's needed for the list UI).
    """
    client = _client()
    if client is None:
        return None
    if not is_available():
        return None

    mode = mode if mode in SEARCH_MODES else "all"
    query_by = SEARCH_MODES[mode]["query_by"]
    query = (q or "").strip() or "*"

    filters = [f"user_id:={int(user_id)}"]
    if kind in ("session", "comparison"):
        filters.append(f"kind:={kind}")
    if shot_type in ("forehand", "backhand", "serve"):
        filters.append(f"shot_type:={shot_type}")
    if comparison_pro:
        # Exact facet filter when provided as a chip; free-text uses query_by=pro
        safe = comparison_pro.replace("`", "")
        filters.append(f"comparison_pro:={safe}")

    params = {
        "q": query,
        "query_by": query_by,
        "filter_by": " && ".join(filters),
        "sort_by": "created_at:desc",
        "per_page": min(max(per_page, 1), 100),
    }

    try:
        ensure_collection()
        result = client.collections[COLLECTION].documents.search(params)
    except Exception as e:
        print(f"[typesense] search failed: {e}")
        return None

    items = []
    for hit in result.get("hits") or []:
        d = hit.get("document") or {}
        items.append(
            {
                "id": int(d["id"]),
                "kind": d.get("kind"),
                "created_at": d.get("created_at_iso") or "",
                "original_filename": d.get("original_filename") or None,
                "shot_type": d.get("shot_type"),
                "comparison_pro": d.get("comparison_pro"),
                "summary": _summary_from_doc(d),
                "title": d.get("title"),
            }
        )
    return {
        "items": items,
        "engine": "typesense",
        "found": result.get("found", len(items)),
        "mode": mode,
    }


def _summary_from_doc(d: dict) -> dict | None:
    out = {}
    if "net_clearance" in d:
        out["net_clearance"] = d["net_clearance"]
    if "n_contacts" in d:
        out["n_contacts"] = d["n_contacts"]
    if "avg_velocity_diff" in d:
        out["avg_velocity_diff"] = d["avg_velocity_diff"]
    return out or None


def search_sqlite(
    db,
    user_id: int,
    *,
    q: str = "",
    mode: str = "all",
    kind: str | None = None,
    shot_type: str | None = None,
    comparison_pro: str | None = None,
) -> dict:
    """Same search semantics as Typesense, backed by SQLite (local fallback)."""
    mode = mode if mode in SEARCH_MODES else "all"
    query = (q or "").strip()

    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]

    if kind in ("session", "comparison"):
        clauses.append("kind = ?")
        params.append(kind)
    if shot_type in ("forehand", "backhand", "serve"):
        clauses.append("shot_type = ?")
        params.append(shot_type)
    if comparison_pro:
        clauses.append("comparison_pro = ? COLLATE NOCASE")
        params.append(comparison_pro)

    if query:
        like = f"%{query}%"
        if mode == "filename":
            clauses.append("original_filename LIKE ?")
            params.append(like)
        elif mode == "pro":
            clauses.append("comparison_pro LIKE ?")
            params.append(like)
        elif mode == "shot_type":
            clauses.append("shot_type LIKE ?")
            params.append(like)
        elif mode == "kind":
            clauses.append("kind LIKE ?")
            params.append(like)
        else:
            # Everything: filename, pro, shot, kind, and a synthesized title feel
            clauses.append(
                "("
                "original_filename LIKE ? OR "
                "comparison_pro LIKE ? OR "
                "shot_type LIKE ? OR "
                "kind LIKE ? OR "
                "(kind = 'comparison' AND (shot_type LIKE ? OR comparison_pro LIKE ?))"
                ")"
            )
            params.extend([like, like, like, like, like, like])

    sql = f"""
        SELECT id, kind, created_at, original_filename, shot_type,
               comparison_pro, summary_json
        FROM analyses
        WHERE {" AND ".join(clauses)}
        ORDER BY id DESC
        LIMIT 100
    """
    rows = db.execute(sql, params).fetchall()
    items = []
    for r in rows:
        items.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "created_at": r["created_at"],
                "original_filename": r["original_filename"],
                "shot_type": r["shot_type"],
                "comparison_pro": r["comparison_pro"],
                "summary": json.loads(r["summary_json"]) if r["summary_json"] else None,
                "title": analysis_title(r["kind"], r["shot_type"], r["comparison_pro"]),
            }
        )
    return {
        "items": items,
        "engine": "sqlite",
        "found": len(items),
        "mode": mode,
    }
