"""Authentication: registration, login, logout, and session helpers.

Security choices:
- Passwords are hashed with werkzeug's ``generate_password_hash`` (pbkdf2:sha256).
  Plaintext passwords are never stored or logged.
- The logged-in user id is kept in Flask's signed-cookie session (httponly,
  samesite=Lax — see app.py), so it can't be read or forged by client JS.
- All SQL uses parameterized queries (no string interpolation) to prevent
  SQL injection.
- A small in-memory throttle slows down credential-stuffing / brute force.
- Login failures return an identical generic message whether the username
  exists or not, to avoid user enumeration.
"""

import re
import time
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from api.db import get_db

auth_bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 200

# ---- in-memory login throttle (per username+IP) ----
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 300
_attempts: dict[str, tuple[int, float]] = {}


def _throttle_key() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?")
    return f"{ip}:{(request.get_json(silent=True) or {}).get('username', '')}"


def _is_throttled(key: str) -> bool:
    rec = _attempts.get(key)
    if not rec:
        return False
    count, first = rec
    if time.time() - first > _WINDOW_SECONDS:
        _attempts.pop(key, None)
        return False
    return count >= _MAX_ATTEMPTS


def _record_failure(key: str) -> None:
    count, first = _attempts.get(key, (0, time.time()))
    if time.time() - first > _WINDOW_SECONDS:
        count, first = 0, time.time()
    _attempts[key] = (count + 1, first)


def _clear_failures(key: str) -> None:
    _attempts.pop(key, None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_user(row) -> dict:
    return {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}


def current_user():
    """Return the logged-in user row, or None."""
    uid = session.get("user_id")
    if uid is None:
        return None
    db = get_db()
    return db.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?", (uid,)
    ).fetchone()


def login_required(fn):
    """Reject unauthenticated requests with 401."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("user_id") is None:
            return jsonify({"error": "authentication required"}), 401
        return fn(*args, **kwargs)

    return wrapper


@auth_bp.route("/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not USERNAME_RE.match(username):
        return (
            jsonify(
                {
                    "error": "Username must be 3–32 characters, letters, numbers, or underscores only."
                }
            ),
            400,
        )
    if not (MIN_PASSWORD_LEN <= len(password) <= MAX_PASSWORD_LEN):
        return (
            jsonify({"error": f"Password must be at least {MIN_PASSWORD_LEN} characters."}),
            400,
        )

    db = get_db()
    existing = db.execute(
        "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    if existing:
        return jsonify({"error": "That username is already taken."}), 409

    cur = db.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, generate_password_hash(password), _now()),
    )
    db.commit()

    session.clear()
    session["user_id"] = cur.lastrowid
    session.permanent = True

    row = db.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return jsonify({"user": _public_user(row)}), 201


@auth_bp.route("/auth/login", methods=["POST"])
def login():
    key = _throttle_key()
    if _is_throttled(key):
        return (
            jsonify({"error": "Too many attempts. Please wait a few minutes and try again."}),
            429,
        )

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    db = get_db()
    row = db.execute(
        "SELECT id, username, password_hash, created_at FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
    ).fetchone()

    if row is None or not check_password_hash(row["password_hash"], password):
        _record_failure(key)
        return jsonify({"error": "Incorrect username or password."}), 401

    _clear_failures(key)
    session.clear()
    session["user_id"] = row["id"]
    session.permanent = True
    return jsonify({"user": _public_user(row)}), 200


@auth_bp.route("/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True}), 200


@auth_bp.route("/auth/me", methods=["GET"])
def me():
    row = current_user()
    if row is None:
        return jsonify({"user": None}), 200
    return jsonify({"user": _public_user(row)}), 200
