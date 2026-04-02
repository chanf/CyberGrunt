"""Tool quality scoring: usage, success rate, and experimental status."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


MIN_CALLS_FOR_EXPERIMENTAL = 5
MIN_SUCCESS_RATE = 0.60

_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()
_db_path = ""


def init(workspace: str) -> None:
    """Initialize the tool quality database in workspace/files."""
    global _conn, _db_path

    db_dir = os.path.join(os.path.abspath(workspace), "files")
    os.makedirs(db_dir, exist_ok=True)
    target_db_path = os.path.join(db_dir, "tool_quality.db")

    with _lock:
        if _conn is not None and _db_path == target_db_path:
            return
        _close_unlocked()
        _conn = sqlite3.connect(target_db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _db_path = target_db_path
        _create_schema_unlocked(_conn)


def close() -> None:
    """Close the active tool quality database connection."""
    with _lock:
        _close_unlocked()


def record_call(
    tool_name: str,
    ok: bool,
    blocked: bool = False,
    error: str = "",
) -> None:
    """Record one tool invocation and update its quality status."""
    name = _normalize_tool_name(tool_name)
    if not name:
        return

    with _lock:
        conn = _conn_or_raise()
        calls, successes, failures, blocked_count = _fetch_counts_unlocked(conn, name)
        calls, successes, failures, blocked_count = _apply_call_result(
            calls,
            successes,
            failures,
            blocked_count,
            ok=ok,
            blocked=blocked,
        )
        success_rate = _compute_success_rate(calls, successes)
        experimental = int(_is_experimental(calls, success_rate))
        with conn:
            _upsert_status_unlocked(
                conn=conn,
                tool_name=name,
                calls=calls,
                successes=successes,
                failures=failures,
                blocked=blocked_count,
                success_rate=success_rate,
                experimental=experimental,
                last_error=error,
            )


def get_tool_status(tool_name: str) -> Dict[str, Any]:
    """Return quality status for a single tool."""
    name = _normalize_tool_name(tool_name)
    if not name:
        return _default_status("")

    with _lock:
        conn = _conn_or_raise()
        row = conn.execute(
            """
            SELECT tool_name, calls, successes, failures, blocked, success_rate, experimental, updated_at, last_error
            FROM tool_quality
            WHERE tool_name = ?
            """,
            (name,),
        ).fetchone()
    return _default_status(name) if row is None else _row_to_dict(row)


def list_tools(limit: int = 50) -> List[Dict[str, Any]]:
    """List tool quality status rows sorted by last update time."""
    safe_limit = max(1, min(int(limit), 200))
    with _lock:
        conn = _conn_or_raise()
        rows = conn.execute(
            """
            SELECT tool_name, calls, successes, failures, blocked, success_rate, experimental, updated_at, last_error
            FROM tool_quality
            ORDER BY updated_at DESC, tool_name ASC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _create_schema_unlocked(conn: sqlite3.Connection) -> None:
    """Create the tool_quality table if it does not exist."""
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_quality (
                tool_name TEXT PRIMARY KEY,
                calls INTEGER NOT NULL,
                successes INTEGER NOT NULL,
                failures INTEGER NOT NULL,
                blocked INTEGER NOT NULL,
                success_rate REAL NOT NULL,
                experimental INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                last_error TEXT
            )
            """
        )


def _close_unlocked() -> None:
    """Close connection without taking lock; caller must hold _lock."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _conn_or_raise() -> sqlite3.Connection:
    """Return active DB connection or raise if module is not initialized."""
    if _conn is None:
        raise RuntimeError("tool_quality not initialized; call init(workspace) first")
    return _conn


def _normalize_tool_name(tool_name: str) -> str:
    """Normalize user-supplied tool name for storage."""
    return (tool_name or "").strip()


def _fetch_counts_unlocked(conn: sqlite3.Connection, tool_name: str) -> Tuple[int, int, int, int]:
    """Load current counters for a tool from DB, defaulting to zeros."""
    row = conn.execute(
        "SELECT calls, successes, failures, blocked FROM tool_quality WHERE tool_name = ?",
        (tool_name,),
    ).fetchone()
    if row is None:
        return 0, 0, 0, 0
    return int(row["calls"]), int(row["successes"]), int(row["failures"]), int(row["blocked"])


def _apply_call_result(
    calls: int,
    successes: int,
    failures: int,
    blocked_count: int,
    *,
    ok: bool,
    blocked: bool,
) -> Tuple[int, int, int, int]:
    """Apply one invocation result to counters and return updated values."""
    calls += 1
    if ok:
        successes += 1
    else:
        failures += 1
    if blocked:
        blocked_count += 1
    return calls, successes, failures, blocked_count


def _compute_success_rate(calls: int, successes: int) -> float:
    """Compute success rate as a value in [0, 1]."""
    if calls <= 0:
        return 1.0
    return float(successes) / float(calls)


def _is_experimental(calls: int, success_rate: float) -> bool:
    """Return whether a tool should be marked experimental."""
    return calls >= MIN_CALLS_FOR_EXPERIMENTAL and success_rate < MIN_SUCCESS_RATE


def _upsert_status_unlocked(
    conn: sqlite3.Connection,
    tool_name: str,
    calls: int,
    successes: int,
    failures: int,
    blocked: int,
    success_rate: float,
    experimental: int,
    last_error: str,
) -> None:
    """Insert or update quality status for one tool."""
    conn.execute(
        """
        INSERT INTO tool_quality (
            tool_name, calls, successes, failures, blocked, success_rate, experimental, updated_at, last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tool_name) DO UPDATE SET
            calls = excluded.calls,
            successes = excluded.successes,
            failures = excluded.failures,
            blocked = excluded.blocked,
            success_rate = excluded.success_rate,
            experimental = excluded.experimental,
            updated_at = excluded.updated_at,
            last_error = excluded.last_error
        """,
        (
            tool_name,
            calls,
            successes,
            failures,
            blocked,
            success_rate,
            experimental,
            _now_iso(),
            (last_error or "")[:500] or None,
        ),
    )


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a sqlite row into public JSON-serializable dict."""
    return {
        "tool_name": row["tool_name"],
        "calls": int(row["calls"]),
        "successes": int(row["successes"]),
        "failures": int(row["failures"]),
        "blocked": int(row["blocked"]),
        "success_rate": float(row["success_rate"]),
        "experimental": bool(row["experimental"]),
        "updated_at": row["updated_at"],
        "last_error": row["last_error"] or "",
    }


def _default_status(name: str) -> Dict[str, Any]:
    """Return default status payload for unseen tools."""
    return {
        "tool_name": name,
        "calls": 0,
        "successes": 0,
        "failures": 0,
        "blocked": 0,
        "success_rate": 1.0,
        "experimental": False,
        "updated_at": "",
        "last_error": "",
    }


def _ensure_ready() -> None:
    """Compatibility guard kept for older callers and tests."""
    _conn_or_raise()


def _now_iso() -> str:
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
