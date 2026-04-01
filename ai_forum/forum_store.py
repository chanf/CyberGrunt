"""SQLite storage layer for the AI collaboration forum."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


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
                    updated_at TEXT NOT NULL
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
                "CREATE INDEX IF NOT EXISTS idx_threads_status_created ON threads(status, created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_replies_thread ON replies(thread_id)"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_thread(self, title: str, body: str, author: str = "developer_ai") -> Dict[str, Any]:
        now = _now_iso()
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO threads (title, body, author, status, created_at, updated_at)
                VALUES (?, ?, ?, 'open', ?, ?)
                """,
                (title, body, author, now, now),
            )
            thread_id = int(cur.lastrowid)
        return self.get_thread(thread_id)

    def create_reply(self, thread_id: int, body: str, author: str = "reviewer_ai") -> Dict[str, Any]:
        now = _now_iso()
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO replies (thread_id, author, body, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, author, body, now),
            )
            reply_id = int(cur.lastrowid)

            if author == "reviewer_ai":
                self._conn.execute(
                    "UPDATE threads SET status = 'replied', updated_at = ? WHERE id = ?",
                    (now, thread_id),
                )
            else:
                self._conn.execute(
                    "UPDATE threads SET updated_at = ? WHERE id = ?",
                    (now, thread_id),
                )

        return {
            "id": reply_id,
            "thread_id": int(thread_id),
            "author": author,
            "body": body,
            "created_at": now,
        }

    def list_threads(self, status: str = "all", limit: int = 50) -> List[Dict[str, Any]]:
        status = status if status in ("all", "open", "replied") else "all"
        limit = max(1, min(int(limit), 200))

        query = (
            """
            SELECT t.id, t.title, t.body, t.author, t.status, t.created_at, t.updated_at,
                   COUNT(r.id) AS reply_count
            FROM threads t
            LEFT JOIN replies r ON r.thread_id = t.id
            WHERE (? = 'all' OR t.status = ?)
            GROUP BY t.id
            ORDER BY t.updated_at DESC, t.id DESC
            LIMIT ?
            """
        )
        with self._lock:
            rows = self._conn.execute(query, (status, status, limit)).fetchall()

        return [
            {
                "id": int(row["id"]),
                "title": row["title"],
                "body": row["body"],
                "author": row["author"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "reply_count": int(row["reply_count"]),
            }
            for row in rows
        ]

    def get_thread(self, thread_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, body, author, status, created_at, updated_at FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
            if row is None:
                return None

            replies = self._conn.execute(
                """
                SELECT id, thread_id, author, body, created_at
                FROM replies
                WHERE thread_id = ?
                ORDER BY id ASC
                """,
                (thread_id,),
            ).fetchall()

        return {
            "id": int(row["id"]),
            "title": row["title"],
            "body": row["body"],
            "author": row["author"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "replies": [
                {
                    "id": int(r["id"]),
                    "thread_id": int(r["thread_id"]),
                    "author": r["author"],
                    "body": r["body"],
                    "created_at": r["created_at"],
                }
                for r in replies
            ],
        }

    def get_oldest_open_thread(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id FROM threads
                WHERE status = 'open'
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None
        return self.get_thread(int(row["id"]))

    def count_open_threads(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM threads WHERE status = 'open'"
            ).fetchone()
        return int(row["c"])



def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
