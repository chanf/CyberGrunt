"""Tool quality scoring: usage, success rate, and experimental status."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


MIN_CALLS_FOR_EXPERIMENTAL = 5
MIN_SUCCESS_RATE = 0.60

_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()
_db_path = ""


def init(workspace: str) -> None:
    global _conn, _db_path
    db_dir = os.path.join(os.path.abspath(workspace), "files")
    os.makedirs(db_dir, exist_ok=True)
    _db_path = os.path.join(db_dir, "tool_quality.db")

    with _lock:
        if _conn is not None:
            return
        _conn = sqlite3.connect(_db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        with _conn:
            _conn.execute(
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


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def record_call(
    tool_name: str,
    ok: bool,
    blocked: bool = False,
    error: str = "",
) -> None:
    _ensure_ready()
    name = (tool_name or "").strip()
    if not name:
        return

    with _lock, _conn:
        row = _conn.execute(
            "SELECT calls, successes, failures, blocked FROM tool_quality WHERE tool_name = ?",
            (name,),
        ).fetchone()
        if row:
            calls = int(row["calls"])
            successes = int(row["successes"])
            failures = int(row["failures"])
            blocked_count = int(row["blocked"])
        else:
            calls = successes = failures = blocked_count = 0

        calls += 1
        if ok:
            successes += 1
        else:
            failures += 1
        if blocked:
            blocked_count += 1

        success_rate = float(successes) / float(calls) if calls > 0 else 1.0
        experimental = 1 if calls >= MIN_CALLS_FOR_EXPERIMENTAL and success_rate < MIN_SUCCESS_RATE else 0

        _conn.execute(
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
                name,
                calls,
                successes,
                failures,
                blocked_count,
                success_rate,
                experimental,
                _now_iso(),
                error[:500] if error else None,
            ),
        )


def get_tool_status(tool_name: str) -> Dict[str, Any]:
    _ensure_ready()
    name = (tool_name or "").strip()
    if not name:
        return _default_status("")

    with _lock:
        row = _conn.execute(
            """
            SELECT tool_name, calls, successes, failures, blocked, success_rate, experimental, updated_at, last_error
            FROM tool_quality
            WHERE tool_name = ?
            """,
            (name,),
        ).fetchone()
    if row is None:
        return _default_status(name)
    return _row_to_dict(row)


def list_tools(limit: int = 50) -> List[Dict[str, Any]]:
    _ensure_ready()
    limit = max(1, min(int(limit), 200))
    with _lock:
        rows = _conn.execute(
            """
            SELECT tool_name, calls, successes, failures, blocked, success_rate, experimental, updated_at, last_error
            FROM tool_quality
            ORDER BY updated_at DESC, tool_name ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
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
    if _conn is None:
        raise RuntimeError("tool_quality not initialized; call init(workspace) first")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
