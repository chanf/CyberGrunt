"""SQLite storage layer for AI collaboration forum (API-first)."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VALID_STATUS = {"pending", "resolved"}


class ForumStore:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")

        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    author TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER NOT NULL,
                    author TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_threads_status_updated ON threads(status, updated_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_replies_thread ON replies(thread_id, id)"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_thread(
        self,
        title: str,
        body: str,
        author: str = "developer_ai",
        status: str = "pending",
    ) -> Dict[str, Any]:
        _assert_non_empty("title", title)
        _assert_non_empty("body", body)
        _assert_non_empty("author", author)
        _assert_status(status)

        now = _now_iso()
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO threads (title, body, author, status, created_at, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (title.strip(), body.strip(), author.strip(), status, now, now, author.strip()),
            )
            thread_id = int(cur.lastrowid)
        return self.get_thread(thread_id)

    def create_reply(self, thread_id: int, body: str, author: str) -> Dict[str, Any]:
        _assert_non_empty("body", body)
        _assert_non_empty("author", author)

        now = _now_iso()
        thread_id = int(thread_id)

        with self._lock, self._conn:
            exists = self._conn.execute("SELECT id FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if not exists:
                raise KeyError(f"thread {thread_id} not found")

            cur = self._conn.execute(
                """
                INSERT INTO replies (thread_id, author, body, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, author.strip(), body.strip(), now),
            )
            reply_id = int(cur.lastrowid)
            self._conn.execute(
                "UPDATE threads SET updated_at = ?, updated_by = ? WHERE id = ?",
                (now, author.strip(), thread_id),
            )

        return {
            "id": reply_id,
            "thread_id": thread_id,
            "author": author.strip(),
            "body": body.strip(),
            "created_at": now,
        }

    def set_thread_status(self, thread_id: int, status: str, updated_by: str) -> Dict[str, Any]:
        _assert_status(status)
        _assert_non_empty("updated_by", updated_by)

        now = _now_iso()
        thread_id = int(thread_id)

        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE threads SET status = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                (status, now, updated_by.strip(), thread_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"thread {thread_id} not found")

        return self.get_thread(thread_id)

    def list_threads(self, status: str = "all", limit: int = 50) -> List[Dict[str, Any]]:
        status = status if status in ("all", "pending", "resolved") else "all"
        limit = max(1, min(int(limit), 200))

        query = """
            SELECT
                t.id,
                t.title,
                t.body,
                t.author,
                t.status,
                t.created_at,
                t.updated_at,
                t.updated_by,
                (SELECT COUNT(*) FROM replies r WHERE r.thread_id = t.id) AS reply_count,
                (SELECT r.author FROM replies r WHERE r.thread_id = t.id ORDER BY r.id DESC LIMIT 1) AS last_reply_author
            FROM threads t
            WHERE (? = 'all' OR t.status = ?)
            ORDER BY t.updated_at DESC, t.id DESC
            LIMIT ?
        """

        with self._lock:
            rows = self._conn.execute(query, (status, status, limit)).fetchall()

        # 为每个帖子加载回复（Web UI 需要）
        result = []
        for row in rows:
            thread_id = int(row["id"])
            summary = self._thread_summary_from_row(row)

            # 加载该帖子的所有回复
            reply_rows = self._conn.execute(
                """
                SELECT id, thread_id, author, body, created_at
                FROM replies
                WHERE thread_id = ?
                ORDER BY id ASC
                """,
                (thread_id,),
            ).fetchall()

            summary["replies"] = [
                {
                    "id": int(r["id"]),
                    "thread_id": int(r["thread_id"]),
                    "author": r["author"],
                    "body": r["body"],
                    "created_at": r["created_at"],
                }
                for r in reply_rows
            ]
            result.append(summary)

        return result

    def get_thread(self, thread_id: int) -> Optional[Dict[str, Any]]:
        thread_id = int(thread_id)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    t.id,
                    t.title,
                    t.body,
                    t.author,
                    t.status,
                    t.created_at,
                    t.updated_at,
                    t.updated_by,
                    (SELECT r.author FROM replies r WHERE r.thread_id = t.id ORDER BY r.id DESC LIMIT 1) AS last_reply_author
                FROM threads t
                WHERE t.id = ?
                """,
                (thread_id,),
            ).fetchone()
            if row is None:
                return None

            reply_rows = self._conn.execute(
                """
                SELECT id, thread_id, author, body, created_at
                FROM replies
                WHERE thread_id = ?
                ORDER BY id ASC
                """,
                (thread_id,),
            ).fetchall()

        replies = [
            {
                "id": int(r["id"]),
                "thread_id": int(r["thread_id"]),
                "author": r["author"],
                "body": r["body"],
                "created_at": r["created_at"],
            }
            for r in reply_rows
        ]

        summary = self._thread_summary_from_row(row)
        summary["replies"] = replies
        summary["reply_count"] = len(replies)
        return summary

    def list_actionable_threads(self, author: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Pending threads where last actor is not the given author."""
        _assert_non_empty("author", author)
        author = author.strip()
        limit = max(1, min(int(limit), 200))

        query = """
            SELECT
                t.id,
                t.title,
                t.body,
                t.author,
                t.status,
                t.created_at,
                t.updated_at,
                t.updated_by,
                (SELECT COUNT(*) FROM replies r WHERE r.thread_id = t.id) AS reply_count,
                (SELECT r.author FROM replies r WHERE r.thread_id = t.id ORDER BY r.id DESC LIMIT 1) AS last_reply_author
            FROM threads t
            WHERE t.status = 'pending'
              AND COALESCE((SELECT r.author FROM replies r WHERE r.thread_id = t.id ORDER BY r.id DESC LIMIT 1), t.author) != ?
            ORDER BY t.updated_at ASC, t.id ASC
            LIMIT ?
        """

        with self._lock:
            rows = self._conn.execute(query, (author, limit)).fetchall()

        # 为每个帖子加载回复（保持与 list_threads 一致）
        result = []
        for row in rows:
            thread_id = int(row["id"])
            summary = self._thread_summary_from_row(row)

            reply_rows = self._conn.execute(
                """
                SELECT id, thread_id, author, body, created_at
                FROM replies
                WHERE thread_id = ?
                ORDER BY id ASC
                """,
                (thread_id,),
            ).fetchall()

            summary["replies"] = [
                {
                    "id": int(r["id"]),
                    "thread_id": int(r["thread_id"]),
                    "author": r["author"],
                    "body": r["body"],
                    "created_at": r["created_at"],
                }
                for r in reply_rows
            ]
            result.append(summary)

        return result

    def _thread_summary_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        last_reply_author = row["last_reply_author"]
        last_actor = last_reply_author or row["author"]
        reply_count = row["reply_count"] if "reply_count" in row.keys() else 0

        return {
            "id": int(row["id"]),
            "title": row["title"],
            "body": row["body"],
            "author": row["author"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
            "last_reply_author": last_reply_author,
            "last_actor": last_actor,
            "reply_count": int(reply_count),
        }


# Compatibility helpers for earlier worker-oriented code
    def count_open_threads(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM threads WHERE status = 'pending'").fetchone()
        return int(row["c"])

    def get_oldest_open_thread(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM threads WHERE status = 'pending' ORDER BY created_at ASC, id ASC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return self.get_thread(int(row["id"]))


def _assert_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _assert_status(status: str) -> None:
    if status not in VALID_STATUS:
        raise ValueError(f"status must be one of {sorted(VALID_STATUS)}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
