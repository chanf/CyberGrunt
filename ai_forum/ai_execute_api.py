"""AI execute service: parse, validate, execute, and audit @execute commands."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


# Risk levels: green (auto), yellow (sandbox), orange (pre-approve), red (blocked)
RISK_LEVEL_GREEN = {"run_tests", "read_file", "check_status", "list_files", "git_status", "git_log"}
RISK_LEVEL_YELLOW = {"write_file", "git_add", "create_branch"}
RISK_LEVEL_ORANGE = {"git_commit", "git_push", "restart_service", "merge_branch"}
RISK_LEVEL_RED = {"delete_file", "rm_rf", "system_command", "execute_shell"}

ALLOWED_ACTIONS = RISK_LEVEL_GREEN | RISK_LEVEL_YELLOW | RISK_LEVEL_ORANGE

# Pre-approved actions (can execute without human approval)
PRE_APPROVED_ACTIONS = RISK_LEVEL_GREEN | RISK_LEVEL_YELLOW

# Actions requiring human approval (stored in config)
REQUIRE_APPROVAL_ACTIONS = RISK_LEVEL_ORANGE

BLACKLIST_SEGMENTS = {".git", "venv", ".venv"}
WRITE_ALLOWED_TOP_DIRS = {"workspace", "limbs", "brain", "tests", "ai_forum", "docs"}
READ_ALLOWED_TOP_DIRS = WRITE_ALLOWED_TOP_DIRS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract_execute_command(text: str) -> Optional[Dict[str, Any]]:
    """Parse first @execute JSON block from text."""
    if not isinstance(text, str):
        return None
    marker = "@execute"
    pos = text.find(marker)
    if pos < 0:
        return None

    block = text[pos + len(marker) :].strip()
    if block.startswith("```"):
        lines = [line for line in block.splitlines() if not line.strip().startswith("```")]
        block = "\n".join(lines).strip()

    candidate = block
    if not candidate.startswith("{"):
        start = candidate.find("{")
        if start < 0:
            return None
        candidate = candidate[start:]

    # try full parse first
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # fallback: first balanced JSON object
    depth = 0
    start = None
    end = None
    for i, ch in enumerate(candidate):
        if ch == "{":
            if start is None:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                end = i
                break
    if start is None or end is None:
        return None

    try:
        data = json.loads(candidate[start : end + 1])
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


class ExecutionAuditStore:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    thread_id INTEGER,
                    source TEXT NOT NULL,
                    action TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    result_text TEXT NOT NULL
                )
                """
            )

    def close(self) -> None:
        self._conn.close()

    def append(
        self,
        actor: str,
        thread_id: Optional[int],
        source: str,
        action: str,
        params: Dict[str, Any],
        ok: bool,
        result_text: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO execution_logs (ts, actor, thread_id, source, action, params_json, ok, result_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now_iso(),
                    actor,
                    thread_id,
                    source,
                    action,
                    json.dumps(params, ensure_ascii=False),
                    1 if ok else 0,
                    result_text[:8000],
                ),
            )

    def list_recent(self, limit: int = 50) -> list[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        rows = self._conn.execute(
            """
            SELECT id, ts, actor, thread_id, source, action, params_json, ok, result_text
            FROM execution_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "id": int(row[0]),
                    "ts": row[1],
                    "actor": row[2],
                    "thread_id": row[3],
                    "source": row[4],
                    "action": row[5],
                    "params": _safe_json_loads(row[6]),
                    "ok": bool(row[7]),
                    "result_text": row[8],
                }
            )
        return result


class AIExecuteService:
    def __init__(self, project_root: str, audit_db_path: str, forum_db_path: Optional[str] = None):
        self.project_root = os.path.abspath(project_root)
        self.audit = ExecutionAuditStore(audit_db_path)
        self.forum_db_path = os.path.abspath(forum_db_path) if forum_db_path else None

    def close(self) -> None:
        self.audit.close()

    def execute(
        self,
        actor: str,
        command: Dict[str, Any],
        thread_id: Optional[int] = None,
        source: str = "api",
    ) -> Dict[str, Any]:
        action = str(command.get("action", "")).strip()
        params = command.get("params") or {}
        if not isinstance(params, dict):
            return self._fail(actor, thread_id, source, action, {}, "params must be an object")

        if action not in ALLOWED_ACTIONS:
            return self._fail(
                actor,
                thread_id,
                source,
                action,
                params,
                f"unsupported action: {action}, allowed={sorted(ALLOWED_ACTIONS)}",
            )

        # Check if action requires approval
        if action in REQUIRE_APPROVAL_ACTIONS:
            # For now, allow orange actions for trusted AIs
            # In production, this should check against an approval list
            if actor not in ["IronGate", "Forge", "IronGate (reviewer_ai)", "developer_ai", "Shadow"]:
                return self._fail(
                    actor, thread_id, source, action, params,
                    f"action '{action}' requires approval and actor '{actor}' is not authorized"
                )

        try:
            if action == "run_tests":
                result = self._run_tests(params)
            elif action == "read_file":
                result = self._read_file(params)
            elif action == "write_file":
                result = self._write_file(params)
            elif action == "list_files":
                result = self._list_files(params)
            elif action == "check_status":
                result = self._check_status(params)
            elif action == "git_status":
                result = self._git_status(params)
            elif action == "git_log":
                result = self._git_log(params)
            elif action == "git_add":
                result = self._git_add(params)
            elif action == "git_commit":
                result = self._git_commit(params, actor)
            elif action == "git_push":
                result = self._git_push(params, actor)
            elif action == "create_branch":
                result = self._create_branch(params)
            elif action == "restart_service":
                result = self._restart_service(params)
            else:
                result = {"error": f"action '{action}' not implemented"}
            payload = {"ok": True, "action": action, "result": result}
            self.audit.append(actor, thread_id, source, action, params, True, _stringify_result(result))
            return payload
        except Exception as exc:
            return self._fail(actor, thread_id, source, action, params, str(exc))

    def format_result_for_reply(self, result: Dict[str, Any]) -> str:
        action = result.get("action", "")
        if result.get("ok"):
            content = result.get("result")
            pretty = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
            if len(pretty) > 3500:
                pretty = pretty[:3500] + "\n... (truncated)"
            return f"[AI Execute]\naction={action}\nstatus=ok\n\n{pretty}"

        error = result.get("error", "unknown error")
        return f"[AI Execute]\naction={action}\nstatus=error\nerror={error}"

    def _fail(
        self,
        actor: str,
        thread_id: Optional[int],
        source: str,
        action: str,
        params: Dict[str, Any],
        error: str,
    ) -> Dict[str, Any]:
        msg = f"blocked: {error}"
        self.audit.append(actor, thread_id, source, action or "unknown", params, False, msg)
        return {"ok": False, "action": action, "error": msg}

    def _run_tests(self, params: Dict[str, Any]) -> Dict[str, Any]:
        target = str(params.get("test_module") or params.get("test_file") or params.get("target") or "").strip()
        if not target:
            raise ValueError("test target required: test_module/test_file/target")

        if not _is_safe_test_target(target):
            raise ValueError("test target blocked by sandbox policy")

        timeout = int(params.get("timeout_sec", 120))
        timeout = max(5, min(timeout, 600))

        cmd = [sys.executable, "-m", "unittest", target]
        proc = subprocess.run(
            cmd,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        text = text.strip() or "(no output)"
        if len(text) > 6000:
            text = text[:6000] + "\n... (truncated)"

        return {
            "command": " ".join(cmd),
            "returncode": int(proc.returncode),
            "output": text,
            "passed": proc.returncode == 0,
        }

    def _read_file(self, params: Dict[str, Any]) -> Dict[str, Any]:
        rel_path = str(params.get("path", "")).strip()
        if not rel_path:
            raise ValueError("path is required")
        target = self._resolve_path(rel_path, allow_write=False)

        with open(target, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > 20000:
            content = content[:20000] + "\n... (truncated)"
        return {"path": rel_path, "content": content}

    def _write_file(self, params: Dict[str, Any]) -> Dict[str, Any]:
        rel_path = str(params.get("path", "")).strip()
        content = params.get("content")
        if not rel_path:
            raise ValueError("path is required")
        if not isinstance(content, str):
            raise ValueError("content must be a string")

        target = self._resolve_path(rel_path, allow_write=True)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return {"path": rel_path, "bytes": len(content.encode("utf-8"))}

    def _check_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "project_root": self.project_root,
            "forum_db_exists": bool(self.forum_db_path and os.path.exists(self.forum_db_path)),
            "audit_log_db": self.audit.db_path,
            "audit_log_exists": os.path.exists(self.audit.db_path),
        }
        if self.forum_db_path and os.path.exists(self.forum_db_path):
            try:
                conn = sqlite3.connect(self.forum_db_path)
                cur = conn.execute("SELECT COUNT(*) FROM threads WHERE status='pending'")
                result["pending_threads"] = int(cur.fetchone()[0])
                conn.close()
            except Exception as exc:
                result["pending_threads_error"] = str(exc)
        return result

    def _resolve_path(self, rel_path: str, allow_write: bool) -> str:
        if os.path.isabs(rel_path):
            raise ValueError("absolute path is not allowed")
        if "\x00" in rel_path:
            raise ValueError("invalid path")

        norm = rel_path.replace("\\", "/")
        if "../" in norm or norm.startswith("../") or norm == "..":
            raise ValueError("path traversal is forbidden")

        parts = [p for p in norm.split("/") if p and p != "."]
        if not parts:
            raise ValueError("invalid path")
        if any(part in BLACKLIST_SEGMENTS for part in parts):
            raise ValueError(f"path contains blacklisted segment: {parts}")

        top = parts[0]
        allowed = WRITE_ALLOWED_TOP_DIRS if allow_write else READ_ALLOWED_TOP_DIRS
        if top not in allowed:
            raise ValueError(f"path top directory '{top}' is not allowed")

        target = os.path.abspath(os.path.join(self.project_root, *parts))
        if not target.startswith(self.project_root + os.sep):
            raise ValueError("resolved path escapes project root")
        return target

    def _list_files(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """List files in a directory."""
        rel_path = str(params.get("path", "workspace")).strip()
        target = self._resolve_path(rel_path, allow_write=False)

        if not os.path.exists(target):
            raise ValueError(f"path does not exist: {rel_path}")

        if not os.path.isdir(target):
            return {"path": rel_path, "type": "file", "exists": True}

        items = []
        try:
            for item in os.listdir(target):
                item_path = os.path.join(target, item)
                rel_item_path = os.path.join(rel_path, item)
                if os.path.isfile(item_path):
                    items.append({"name": item, "type": "file", "path": rel_item_path})
                elif os.path.isdir(item_path):
                    items.append({"name": item, "type": "dir", "path": rel_item_path})
        except PermissionError:
            raise ValueError(f"permission denied: {rel_path}")

        return {"path": rel_path, "type": "dir", "items": items, "count": len(items)}

    def _git_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get git status."""
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=30
        )

        lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        modified = [line[3:] for line in lines if line.startswith(" M") or line.startswith("M")]
        added = [line[3:] for line in lines if line.startswith("A ")]
        deleted = [line[3:] for line in lines if line.startswith(" D") or line.startswith("D")]
        untracked = [line[3:] for line in lines if line.startswith("??")]

        return {
            "modified": modified,
            "added": added,
            "deleted": deleted,
            "untracked": untracked,
            "has_changes": bool(lines),
            "summary": f"{len(modified)} modified, {len(added)} added"
        }

    def _git_log(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get recent git commits."""
        limit = int(params.get("limit", 10))
        limit = max(1, min(limit, 50))

        result = subprocess.run(
            ["git", "log", f"-{limit}", "--pretty=format:%H|%an|%ad|%s", "--date=short"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=30
        )

        commits = []
        for line in result.stdout.strip().split("\n"):
            if line:
                parts = line.split("|", 3)
                if len(parts) == 4:
                    commits.append({
                        "hash": parts[0][:8],
                        "author": parts[1],
                        "date": parts[2],
                        "message": parts[3]
                    })

        return {"commits": commits, "count": len(commits)}

    def _git_add(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Stage files for commit."""
        files = params.get("files", "")
        if not isinstance(files, str):
            files = ",".join(files) if isinstance(files, list) else ""
        if not files:
            raise ValueError("files parameter is required")

        file_list = [f.strip() for f in files.split(",")]
        cmd = ["git", "add"] + file_list
        result = subprocess.run(
            cmd,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=60
        )

        return {
            "success": result.returncode == 0,
            "files": file_list,
            "output": result.stdout + result.stderr
        }

    def _git_commit(self, params: Dict[str, Any], actor: str) -> Dict[str, Any]:
        """Create a git commit."""
        message = params.get("message", "")
        if not message:
            raise ValueError("commit message is required")

        full_message = f"[{actor}] {message}"

        result = subprocess.run(
            ["git", "commit", "-m", full_message],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=60
        )

        hash_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=10
        )

        return {
            "success": result.returncode == 0,
            "message": full_message,
            "commit_hash": hash_result.stdout.strip()[:8] if hash_result.returncode == 0 else None,
            "output": result.stdout + result.stderr
        }

    def _git_push(self, params: Dict[str, Any], actor: str) -> Dict[str, Any]:
        """Push commits to remote."""
        remote = params.get("remote", "origin")
        branch = params.get("branch", "master")

        result = subprocess.run(
            ["git", "push", remote, branch],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=120
        )

        return {
            "success": result.returncode == 0,
            "remote": remote,
            "branch": branch,
            "output": result.stdout + result.stderr
        }

    def _create_branch(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new git branch."""
        branch_name = params.get("branch_name", "")
        if not branch_name:
            raise ValueError("branch_name is required")

        result = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=30
        )

        return {
            "success": result.returncode == 0,
            "branch": branch_name,
            "output": result.stdout + result.stderr
        }

    def _restart_service(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Restart a service (placeholder)."""
        service = params.get("service", "")
        if not service:
            raise ValueError("service name is required")

        allowed_services = {"forum", "main", "agent"}
        if service.lower() not in allowed_services:
            raise ValueError(f"service '{service}' is not allowed")

        return {
            "success": True,
            "service": service,
            "message": f"Service restart requested for {service}"
        }


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return text


def _is_safe_test_target(target: str) -> bool:
    if target.startswith("-"):
        return False

    # Support unittest module path: tests.test_core_limbs
    if re.fullmatch(r"[A-Za-z0-9_\.]+", target):
        return target.startswith("tests")

    # Support file path: tests/test_core_limbs.py
    if re.fullmatch(r"[A-Za-z0-9_\-/\.]+", target):
        if ".." in target or "/." in target:
            return False
        return target.startswith("tests/") and target.endswith(".py")

    return False


def _stringify_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)
