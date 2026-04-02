"""
Workflow Store - Data persistence layer for the workflow management system.
Implements single-assignee workflow model to avoid responsibility diffusion.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


class WorkflowStore:
    """SQLite-based storage for workflows and comments."""

    VALID_STATUSES = {"open", "assigned", "in_progress", "completed", "blocked"}
    VALID_TYPES = {"feature", "bug", "refactor", "test", "doc"}
    VALID_PRIORITIES = {"p0", "p1", "p2", "p3"}
    VALID_COMMENT_TYPES = {"comment", "status_change", "claim", "unclaim", "reassign"}

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._lock:
            conn = self._get_conn()
            try:
                # Workflows table
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workflows (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        type TEXT NOT NULL CHECK(type IN ('feature', 'bug', 'refactor', 'test', 'doc')),
                        priority TEXT NOT NULL CHECK(priority IN ('p0', 'p1', 'p2', 'p3')),
                        status TEXT NOT NULL CHECK(status IN ('open', 'assigned', 'in_progress', 'completed', 'blocked')) DEFAULT 'open',
                        assignee TEXT,
                        claimed_at TEXT,
                        created_by TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        updated_by TEXT NOT NULL,
                        completed_at TEXT,
                        estimate_hours INTEGER,
                        actual_hours INTEGER,
                        related_thread_id INTEGER,
                        blocked_by_id INTEGER,
                        FOREIGN KEY(related_thread_id) REFERENCES threads(id),
                        FOREIGN KEY(blocked_by_id) REFERENCES workflows(id)
                    )
                """
                )

                # Comments table
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workflow_comments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workflow_id INTEGER NOT NULL,
                        author TEXT NOT NULL,
                        comment_type TEXT NOT NULL CHECK(comment_type IN ('comment', 'status_change', 'claim', 'unclaim', 'reassign')),
                        body TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
                    )
                """
                )

                # Indexes for performance
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_assignee ON workflows(assignee)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_type ON workflows(type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_priority ON workflows(priority)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_comments_workflow ON workflow_comments(workflow_id)")

                conn.commit()
            finally:
                conn.close()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ==================== Workflow CRUD ====================

    def create_workflow(
        self,
        title: str,
        description: str,
        workflow_type: str,
        priority: str,
        created_by: str,
        estimate_hours: Optional[int] = None,
        related_thread_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a new workflow."""
        if workflow_type not in self.VALID_TYPES:
            raise ValueError(f"Invalid type: {workflow_type}")
        if priority not in self.VALID_PRIORITIES:
            raise ValueError(f"Invalid priority: {priority}")

        now = self._now_iso()

        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO workflows (
                        title, description, type, priority, status,
                        created_by, created_at, updated_at, updated_by,
                        estimate_hours, related_thread_id
                    ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
                    """,
                    (title, description, workflow_type, priority, created_by, now, now, created_by, estimate_hours, related_thread_id),
                )
                workflow_id = cursor.lastrowid
                conn.commit()

                # Return the created workflow directly from DB
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                return self._workflow_from_row(row, conn)
            finally:
                conn.close()

    def get_workflow_by_id(self, workflow_id: int) -> Dict[str, Any]:
        """Get workflow by ID."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                if not row:
                    raise ValueError(f"Workflow {workflow_id} not found")
                return self._workflow_from_row(row, conn)
            finally:
                conn.close()

    def list_workflows(
        self,
        status: Optional[str] = None,
        assignee: Optional[str] = None,
        workflow_type: Optional[str] = None,
        priority: Optional[str] = None,
        created_by: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List workflows with optional filters."""
        conditions = []
        params = []

        if status:
            if status not in self.VALID_STATUSES:
                raise ValueError(f"Invalid status: {status}")
            conditions.append("status = ?")
            params.append(status)

        if assignee:
            conditions.append("assignee = ?")
            params.append(assignee)

        if workflow_type:
            if workflow_type not in self.VALID_TYPES:
                raise ValueError(f"Invalid type: {workflow_type}")
            conditions.append("type = ?")
            params.append(workflow_type)

        if priority:
            if priority not in self.VALID_PRIORITIES:
                raise ValueError(f"Invalid priority: {priority}")
            conditions.append("priority = ?")
            params.append(priority)

        if created_by:
            conditions.append("created_by = ?")
            params.append(created_by)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_clause = f"LIMIT {limit}"

        query = f"""
            SELECT * FROM workflows
            {where_clause}
            ORDER BY updated_at DESC, id DESC
            {limit_clause}
        """

        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(query, params).fetchall()
                return [self._workflow_from_row(row, conn) for row in rows]
            finally:
                conn.close()

    def update_workflow(
        self,
        workflow_id: int,
        updated_by: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        estimate_hours: Optional[int] = None,
        actual_hours: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update workflow fields."""
        updates = []
        params = []

        if title is not None:
            updates.append("title = ?")
            params.append(title)

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if status is not None:
            if status not in self.VALID_STATUSES:
                raise ValueError(f"Invalid status: {status}")
            updates.append("status = ?")
            params.append(status)

        if estimate_hours is not None:
            updates.append("estimate_hours = ?")
            params.append(estimate_hours)

        if actual_hours is not None:
            updates.append("actual_hours = ?")
            params.append(actual_hours)

        if not updates:
            raise ValueError("No fields to update")

        updates.append("updated_at = ?")
        updates.append("updated_by = ?")
        now = self._now_iso()
        params.extend([now, updated_by, workflow_id])

        query = f"UPDATE workflows SET {', '.join(updates)} WHERE id = ?"

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(query, params)
                conn.commit()
                # Return updated workflow directly from DB
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                if not row:
                    raise ValueError(f"Workflow {workflow_id} not found")
                return self._workflow_from_row(row, conn)
            finally:
                conn.close()

    # ==================== Workflow Operations ====================

    def claim_workflow(self, workflow_id: int, assignee: str) -> Dict[str, Any]:
        """Claim a workflow (only open workflows can be claimed)."""
        with self._lock:
            conn = self._get_conn()
            try:
                # Check current status
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                if not row:
                    raise ValueError(f"Workflow {workflow_id} not found")
                if row["status"] != "open":
                    raise ValueError(f"Cannot claim workflow with status: {row['status']}")
                if row["assignee"] is not None:
                    raise ValueError(f"Workflow already assigned to: {row['assignee']}")

                now = self._now_iso()
                conn.execute(
                    """
                    UPDATE workflows
                    SET status = 'assigned', assignee = ?, claimed_at = ?,
                        updated_at = ?, updated_by = ?
                    WHERE id = ?
                    """,
                    (assignee, now, now, assignee, workflow_id),
                )
                conn.commit()

                # Add claim comment
                self._add_comment_internal(conn, workflow_id, assignee, "claim", f"Claimed by {assignee}")

                # Return updated workflow
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                return self._workflow_from_row(row, conn)
            finally:
                conn.close()

    def unclaim_workflow(self, workflow_id: int, assignee: str, reason: str) -> Dict[str, Any]:
        """Unclaim a workflow (only current assignee can unclaim)."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                if not row:
                    raise ValueError(f"Workflow {workflow_id} not found")
                if row["assignee"] != assignee:
                    raise ValueError(f"Only current assignee can unclaim. Current: {row['assignee']}, Requested: {assignee}")

                now = self._now_iso()
                conn.execute(
                    """
                    UPDATE workflows
                    SET status = 'open', assignee = NULL, claimed_at = NULL,
                        updated_at = ?, updated_by = ?
                    WHERE id = ?
                    """,
                    (now, assignee, workflow_id),
                )
                conn.commit()

                # Add unclaim comment
                self._add_comment_internal(conn, workflow_id, assignee, "unclaim", f"Unclaimed: {reason}")

                # Return updated workflow
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                return self._workflow_from_row(row, conn)
            finally:
                conn.close()

    def reassign_workflow(
        self, workflow_id: int, from_assignee: str, to_assignee: str, reason: str
    ) -> Dict[str, Any]:
        """Reassign workflow to another person."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                if not row:
                    raise ValueError(f"Workflow {workflow_id} not found")
                if row["assignee"] != from_assignee:
                    raise ValueError(f"Only current assignee can reassign. Current: {row['assignee']}, Requested: {from_assignee}")

                now = self._now_iso()
                conn.execute(
                    """
                    UPDATE workflows
                    SET assignee = ?, claimed_at = ?, updated_at = ?, updated_by = ?
                    WHERE id = ?
                    """,
                    (to_assignee, now, now, from_assignee, workflow_id),
                )
                conn.commit()

                # Add reassign comment
                self._add_comment_internal(
                    conn, workflow_id, from_assignee, "reassign", f"Reassigned to {to_assignee}: {reason}"
                )

                # Return updated workflow
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                return self._workflow_from_row(row, conn)
            finally:
                conn.close()

    def set_workflow_status(self, workflow_id: int, status: str, updated_by: str, note: Optional[str] = None) -> Dict[str, Any]:
        """Update workflow status."""
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")

        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                if not row:
                    raise ValueError(f"Workflow {workflow_id} not found")

                # Only assignee can change status (except for open workflows)
                if row["status"] != "open" and row["assignee"] != updated_by:
                    raise ValueError(f"Only assignee can change status. Current assignee: {row['assignee']}")

                updates = ["status = ?", "updated_at = ?", "updated_by = ?"]
                params = [status, self._now_iso(), updated_by]

                # Set completed_at when status is completed
                if status == "completed" and row["status"] != "completed":
                    updates.append("completed_at = ?")
                    params.append(self._now_iso())

                params.append(workflow_id)
                query = f"UPDATE workflows SET {', '.join(updates)} WHERE id = ?"

                conn.execute(query, params)
                conn.commit()

                # Add status change comment
                comment_body = f"Status changed to '{status}'"
                if note:
                    comment_body += f": {note}"
                self._add_comment_internal(conn, workflow_id, updated_by, "status_change", comment_body)

                # Return updated workflow
                row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
                return self._workflow_from_row(row, conn)
            finally:
                conn.close()

    # ==================== Comments ====================

    def add_comment(self, workflow_id: int, author: str, body: str, comment_type: str = "comment") -> Dict[str, Any]:
        """Add a comment to a workflow."""
        if comment_type not in self.VALID_COMMENT_TYPES:
            raise ValueError(f"Invalid comment type: {comment_type}")

        with self._lock:
            conn = self._get_conn()
            try:
                comment_id = self._add_comment_internal(conn, workflow_id, author, comment_type, body)
                conn.commit()

                # Return the created comment directly from DB
                row = conn.execute("SELECT * FROM workflow_comments WHERE id = ?", (comment_id,)).fetchone()
                return self._comment_from_row(row)
            finally:
                conn.close()

    def _add_comment_internal(
        self, conn: sqlite3.Connection, workflow_id: int, author: str, comment_type: str, body: str
    ) -> int:
        """Internal method to add comment (assumes lock held)."""
        cursor = conn.execute(
            """
            INSERT INTO workflow_comments (workflow_id, author, comment_type, body, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (workflow_id, author, comment_type, body, self._now_iso()),
        )
        return cursor.lastrowid

    def get_comment_by_id(self, comment_id: int) -> Dict[str, Any]:
        """Get comment by ID."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM workflow_comments WHERE id = ?", (comment_id,)).fetchone()
                if not row:
                    raise ValueError(f"Comment {comment_id} not found")
                return self._comment_from_row(row)
            finally:
                conn.close()

    def list_workflow_comments(self, workflow_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """List comments for a workflow."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM workflow_comments
                    WHERE workflow_id = ?
                    ORDER BY created_at ASC
                    LIMIT ?
                    """,
                    (workflow_id, limit),
                ).fetchall()
                return [self._comment_from_row(row) for row in rows]
            finally:
                conn.close()

    # ==================== Helpers ====================

    def _workflow_from_row(self, row: sqlite3.Row, conn: sqlite3.Connection = None) -> Dict[str, Any]:
        """Convert database row to workflow dict."""
        # Count comments - use provided conn to avoid nested locks
        comment_count = 0
        if conn:
            count_row = conn.execute(
                "SELECT COUNT(*) as count FROM workflow_comments WHERE workflow_id = ?", (row["id"],)
            ).fetchone()
            comment_count = count_row["count"] if count_row else 0

        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "type": row["type"],
            "priority": row["priority"],
            "status": row["status"],
            "assignee": row["assignee"],
            "claimed_at": row["claimed_at"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
            "completed_at": row["completed_at"],
            "estimate_hours": row["estimate_hours"],
            "actual_hours": row["actual_hours"],
            "related_thread_id": row["related_thread_id"],
            "blocked_by_id": row["blocked_by_id"],
            "comment_count": comment_count,
        }

    def _comment_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert database row to comment dict."""
        return {
            "id": row["id"],
            "workflow_id": row["workflow_id"],
            "author": row["author"],
            "comment_type": row["comment_type"],
            "body": row["body"],
            "created_at": row["created_at"],
        }

    def _count_comments(self, workflow_id: int) -> int:
        """Count comments for a workflow."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as count FROM workflow_comments WHERE workflow_id = ?", (workflow_id,)
                ).fetchone()
                return row["count"] if row else 0
            finally:
                conn.close()
