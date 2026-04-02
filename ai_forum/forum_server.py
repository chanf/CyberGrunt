"""Standalone API-first forum server for two AI collaborators."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.parse
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Dict, Optional, Tuple

if __package__ is None or __package__ == "":
    # Allow running as script: ./venv/bin/python ai_forum/forum_server.py
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_forum.forum_store import ForumStore
from ai_forum.ai_execute_api import AIExecuteService, extract_execute_command

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ai_forum")


class EventBus:
    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def publish(self, event_type: str, content: Dict[str, Any]) -> None:
        packet = {
            "type": event_type,
            "content": content,
            "ts": time.time(),
        }
        with self._lock:
            targets = list(self._subs)
        for q in targets:
            q.put(packet)


class ForumApp:
    def __init__(self, store: ForumStore, executor: AIExecuteService, bus: Optional[EventBus] = None):
        self.store = store
        self.executor = executor
        self.bus = bus or EventBus()


APP: Optional[ForumApp] = None


def _resolve_config_path() -> str:
    env_path = os.environ.get("FORUM_CONFIG")
    if env_path:
        return os.path.abspath(env_path)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "config.json"),
        os.path.join(base_dir, "..", "config.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return os.path.abspath(path)

    return os.path.abspath(candidates[-1])


def load_config() -> Tuple[Dict[str, Any], str]:
    config_path = _resolve_config_path()
    if not os.path.exists(config_path):
        return {}, config_path

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    return config, config_path


def resolve_forum_settings(config: Dict[str, Any], config_path: str) -> Dict[str, Any]:
    forum_cfg = dict(config.get("forum", {}))

    defaults = {
        "port": 8090,
        "db_path": "./ai_forum/ai_forum.db",
        "execution_log_db_path": "./ai_forum/execution_log.db",
        "default_limit": 50,
    }

    merged = {**defaults, **forum_cfg}

    base_dir = os.path.dirname(os.path.abspath(config_path))
    db_path = merged.get("db_path", defaults["db_path"])
    if not os.path.isabs(db_path):
        db_path = os.path.abspath(os.path.join(base_dir, db_path))

    exec_db = merged.get("execution_log_db_path", defaults["execution_log_db_path"])
    if not os.path.isabs(exec_db):
        exec_db = os.path.abspath(os.path.join(base_dir, exec_db))

    merged["db_path"] = db_path
    merged["execution_log_db_path"] = exec_db
    merged["port"] = int(merged["port"])
    merged["default_limit"] = int(merged["default_limit"])
    return merged


class ForumHandler(BaseHTTPRequestHandler):
    def handle(self) -> None:
        """Suppress common disconnect noise from SSE clients."""
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send_html(HTML_PAGE)
            return

        if path == "/status":
            self._send_status_page()
            return

        if path == "/log":
            self._send_log_page()
            return

        if path == "/healthz":
            self._send_json(200, {"ok": True})
            return

        if path == "/api/threads":
            self._handle_list_threads(parsed)
            return

        if path.startswith("/api/threads/") and path.endswith("/actionable"):
            self._send_json(404, {"error": "not found"})
            return

        if path.startswith("/api/threads/"):
            self._handle_get_thread(path)
            return

        if path == "/api/actionable":
            self._handle_actionable(parsed)
            return

        if path == "/api/ai/execution_logs":
            self._handle_execution_logs(parsed)
            return

        if path in ("/api/events", "/events"):
            self._handle_events()
            return

        if path == "/api/log":
            self._handle_get_log()
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/threads":
            self._handle_create_thread()
            return

        if path.startswith("/api/threads/") and path.endswith("/replies"):
            self._handle_create_reply(path)
            return

        if path.startswith("/api/threads/") and path.endswith("/status"):
            self._handle_set_status(path)
            return

        if path == "/api/ai/execute":
            self._handle_ai_execute()
            return

        if path == "/api/log":
            self._handle_save_log()
            return

        self._send_json(404, {"error": "not found"})

    def _handle_list_threads(self, parsed: urllib.parse.ParseResult) -> None:
        params = urllib.parse.parse_qs(parsed.query)
        status = params.get("status", ["all"])[0]
        limit = self._get_limit(params)

        threads = APP.store.list_threads(status=status, limit=limit)
        self._send_json(200, {"threads": threads})

    def _handle_get_thread(self, path: str) -> None:
        thread_id = self._extract_thread_id(path)
        if thread_id is None:
            self._send_json(400, {"error": "invalid thread id"})
            return

        thread = APP.store.get_thread(thread_id)
        if not thread:
            self._send_json(404, {"error": "thread not found"})
            return

        self._send_json(200, {"thread": thread})

    def _handle_actionable(self, parsed: urllib.parse.ParseResult) -> None:
        params = urllib.parse.parse_qs(parsed.query)
        author = params.get("author", [""])[0].strip()
        if not author:
            self._send_json(400, {"error": "author is required"})
            return

        limit = self._get_limit(params)
        threads = APP.store.list_actionable_threads(author=author, limit=limit)
        self._send_json(200, {"author": author, "threads": threads})

    def _handle_create_thread(self) -> None:
        body = self._read_json_body()
        if body is None:
            return

        try:
            author = str(body.get("author", "")).strip()
            title = str(body.get("title", "")).strip()
            content = str(body.get("body", "")).strip()
            status = str(body.get("status", "pending")).strip() or "pending"

            thread = APP.store.create_thread(title=title, body=content, author=author, status=status)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return

        execution = self._maybe_auto_execute_from_text(
            actor=author,
            thread_id=int(thread["id"]),
            text=content,
            source="thread_create",
        )
        if execution is not None:
            thread = APP.store.get_thread(int(thread["id"]))

        APP.bus.publish("thread_created", {"thread": thread})
        self._send_json(201, {"thread": thread, "execution": execution})

    def _handle_create_reply(self, path: str) -> None:
        thread_id = self._extract_thread_id(path)
        if thread_id is None:
            self._send_json(400, {"error": "invalid thread id"})
            return

        body = self._read_json_body()
        if body is None:
            return

        try:
            author = str(body.get("author", "")).strip()
            content = str(body.get("body", "")).strip()
            reply = APP.store.create_reply(thread_id=thread_id, body=content, author=author)
            thread = APP.store.get_thread(thread_id)
        except KeyError as exc:
            self._send_json(404, {"error": str(exc)})
            return
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return

        execution = self._maybe_auto_execute_from_text(
            actor=author,
            thread_id=thread_id,
            text=content,
            source="thread_reply",
        )
        if execution is not None:
            thread = APP.store.get_thread(thread_id)

        APP.bus.publish("reply_created", {"thread": thread, "reply": reply})
        self._send_json(201, {"thread": thread, "reply": reply, "execution": execution})

    def _handle_set_status(self, path: str) -> None:
        thread_id = self._extract_thread_id(path)
        if thread_id is None:
            self._send_json(400, {"error": "invalid thread id"})
            return

        body = self._read_json_body()
        if body is None:
            return

        try:
            author = str(body.get("author", "")).strip()
            status = str(body.get("status", "")).strip()
            note = str(body.get("note", "")).strip()

            thread = APP.store.set_thread_status(thread_id=thread_id, status=status, updated_by=author)
            reply = None
            if note:
                reply = APP.store.create_reply(thread_id=thread_id, body=f"[状态更新] {note}", author=author)
                thread = APP.store.get_thread(thread_id)
        except KeyError as exc:
            self._send_json(404, {"error": str(exc)})
            return
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return

        APP.bus.publish("status_changed", {"thread": thread, "reply": reply})
        self._send_json(200, {"thread": thread, "reply": reply})

    def _handle_ai_execute(self) -> None:
        body = self._read_json_body()
        if body is None:
            return

        actor = str(body.get("author", "")).strip()
        if not actor:
            self._send_json(400, {"error": "author is required"})
            return

        raw_thread_id = body.get("thread_id")
        thread_id: Optional[int] = None
        if raw_thread_id is not None:
            try:
                thread_id = int(raw_thread_id)
            except Exception:
                self._send_json(400, {"error": "thread_id must be integer"})
                return

        command = body.get("command")
        if not isinstance(command, dict):
            text = str(body.get("body") or body.get("text") or "")
            command = extract_execute_command(text)
        if not isinstance(command, dict):
            self._send_json(400, {"error": "missing execute command"})
            return

        source = str(body.get("source") or "api")
        result = APP.executor.execute(
            actor=actor,
            command=command,
            thread_id=thread_id,
            source=source,
        )

        reply = None
        thread = None
        if body.get("auto_reply", True) and thread_id is not None:
            report = APP.executor.format_result_for_reply(result)
            try:
                reply = APP.store.create_reply(thread_id=thread_id, body=report, author="executor_bot")
                thread = APP.store.get_thread(thread_id)
                APP.bus.publish("reply_created", {"thread": thread, "reply": reply})
            except Exception as exc:
                log.error("execute auto-reply failed in thread %s: %s", thread_id, exc)

        self._send_json(200, {"result": result, "thread": thread, "reply": reply})

    def _handle_execution_logs(self, parsed: urllib.parse.ParseResult) -> None:
        params = urllib.parse.parse_qs(parsed.query)
        limit = self._get_limit(params)
        logs = APP.executor.audit.list_recent(limit=limit)
        self._send_json(200, {"logs": logs})

    def _maybe_auto_execute_from_text(
        self,
        actor: str,
        thread_id: int,
        text: str,
        source: str,
    ) -> Optional[Dict[str, Any]]:
        if actor == "executor_bot":
            return None

        command = extract_execute_command(text)
        if not command:
            return None

        result = APP.executor.execute(
            actor=actor,
            command=command,
            thread_id=thread_id,
            source=source,
        )
        report = APP.executor.format_result_for_reply(result)
        try:
            reply = APP.store.create_reply(thread_id=thread_id, body=report, author="executor_bot")
            thread = APP.store.get_thread(thread_id)
            APP.bus.publish("reply_created", {"thread": thread, "reply": reply})
            return {"command": command, "result": result, "reply_id": reply["id"]}
        except Exception as exc:
            log.error("auto execute writeback failed in thread %s: %s", thread_id, exc)
            return {"command": command, "result": result, "error": str(exc)}

    def _handle_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        sub = APP.bus.subscribe()
        try:
            self._write_sse("connected", {"message": "connected"})
            while True:
                try:
                    packet = sub.get(timeout=15)
                    self._write_sse(packet["type"], packet["content"])
                except queue.Empty:
                    self._write_sse("heartbeat", {"ok": True})
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            APP.bus.unsubscribe(sub)

    def _extract_thread_id(self, path: str) -> Optional[int]:
        # /api/threads/{id}
        # /api/threads/{id}/replies
        # /api/threads/{id}/status
        parts = [p for p in path.split("/") if p]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "threads":
            return None
        if not parts[2].isdigit():
            return None
        return int(parts[2])

    def _get_limit(self, params: Dict[str, Any]) -> int:
        raw = params.get("limit", [str(SETTINGS.get("default_limit", 50))])[0]
        try:
            return max(1, min(int(raw), 200))
        except ValueError:
            return int(SETTINGS.get("default_limit", 50))

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data
        except Exception as exc:
            self._send_json(400, {"error": f"invalid json body: {exc}"})
            return None

    def _write_sse(self, event_type: str, payload: Dict[str, Any]) -> None:
        packet = {
            "type": event_type,
            "content": payload,
            "ts": time.time(),
        }
        self.wfile.write(f"event: {event_type}\n".encode("utf-8"))
        self.wfile.write(f"data: {json.dumps(packet, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_status_page(self) -> None:
        self._send_html(STATUS_PAGE)

    def _send_log_page(self) -> None:
        self._send_html(LOG_PAGE)

    def _handle_get_log(self) -> None:
        """Get Shadow's work log entries."""
        import os
        import json
        log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_reports", "shadow_work_log.txt")
        if not os.path.exists(log_path):
            self._send_json(200, {"entries": []})
            return

        try:
            with open(log_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Parse log entries (format: TIMESTAMP || CATEGORY || JSON_CONTENT)
            entries = []
            for line in content.strip().split("\n"):
                if " || " in line:
                    parts = line.split(" || ", 2)
                    if len(parts) == 3:
                        try:
                            # Content is JSON-encoded to preserve newlines
                            content_text = json.loads(parts[2])
                            entries.append({"timestamp": parts[0], "category": parts[1], "content": content_text})
                        except json.JSONDecodeError:
                            # Fallback for old format
                            entries.append({"timestamp": parts[0], "category": parts[1], "content": parts[2]})

            # Reverse to show newest first
            entries.reverse()
            self._send_json(200, {"entries": entries})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _handle_save_log(self) -> None:
        """Save a new work log entry."""
        import os
        import json
        from datetime import datetime

        body = self._read_json_body()
        if body is None:
            return

        category = str(body.get("category", "general")).strip()
        content = str(body.get("content", "")).strip()

        if not content:
            self._send_json(400, {"error": "content is required"})
            return

        log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_reports", "shadow_work_log.txt")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Encode content as JSON to preserve newlines
        content_json = json.dumps(content, ensure_ascii=False)
        log_entry = f"{timestamp} || {category} || {content_json}\n"

        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
            self._send_json(201, {"entry": {"timestamp": timestamp, "category": category, "content": content}})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


SETTINGS: Dict[str, Any] = {}


def create_app(
    store: ForumStore,
    db_path: Optional[str] = None,
    execution_log_db_path: Optional[str] = None,
    project_root: Optional[str] = None,
) -> ForumApp:
    project_root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    forum_db_path = db_path or getattr(store, "db_path", None)
    audit_db_path = (
        execution_log_db_path
        or SETTINGS.get("execution_log_db_path")
        or os.path.join(project_root, "ai_forum", "execution_log.db")
    )
    executor = AIExecuteService(
        project_root=project_root,
        audit_db_path=audit_db_path,
        forum_db_path=forum_db_path,
    )
    return ForumApp(store=store, executor=executor, bus=EventBus())


def create_server(app: ForumApp, host: str = "0.0.0.0", port: int = 8090) -> ThreadedHTTPServer:
    global APP
    APP = app
    return ThreadedHTTPServer((host, port), ForumHandler)


def main() -> None:
    global SETTINGS

    config, config_path = load_config()
    SETTINGS = resolve_forum_settings(config, config_path)

    store = ForumStore(SETTINGS["db_path"])
    app = create_app(
        store=store,
        db_path=SETTINGS["db_path"],
        execution_log_db_path=SETTINGS.get("execution_log_db_path"),
    )
    server = create_server(app, host="0.0.0.0", port=SETTINGS["port"])

    log.info("AI forum server started at http://localhost:%d", SETTINGS["port"])

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down forum server...")
    finally:
        server.shutdown()
        app.executor.close()
        store.close()


HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI 协作论坛看板</title>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    :root {
      --bg: #f5f7fb;
      --card: #ffffff;
      --ink: #0f172a;
      --sub: #475569;
      --line: #dbe3ef;
      --brand: #2563eb;
      --ok: #16a34a;
      --warn: #d97706;
      --user: #dbeafe;
      --bot: #ecfeff;
      --shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 10% 20%, #e2e8f0 0%, transparent 40%),
        radial-gradient(circle at 90% 80%, #dbeafe 0%, transparent 35%),
        var(--bg);
      color: var(--ink);
      font-family: "Source Han Sans SC", "PingFang SC", "Inter", sans-serif;
    }

    .wrap { max-width: 1080px; margin: 0 auto; padding: 20px; }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: var(--shadow);
      padding: 16px;
      margin-bottom: 14px;
    }
    .topbar {
      display: flex;
      gap: 12px;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
    }
    .title {
      margin: 0;
      font-size: 22px;
      letter-spacing: 0.5px;
    }
    .subtitle {
      margin: 4px 0 0;
      color: var(--sub);
      font-size: 13px;
    }
    .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .btn, .link-btn {
      border: 1px solid var(--line);
      background: #eff6ff;
      color: #1d4ed8;
      border-radius: 10px;
      padding: 7px 12px;
      font-size: 13px;
      cursor: pointer;
      text-decoration: none;
      transition: 0.18s ease;
    }
    .btn:hover, .link-btn:hover { background: #dbeafe; }
    .btn.active { background: #1d4ed8; color: #ffffff; border-color: #1d4ed8; }
    .btn.subtle { background: #f8fafc; color: #334155; }

    .status-grid {
      display: grid;
      grid-template-columns: 1fr 1fr 2fr;
      gap: 12px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
    }
    .pill.warn { background: #fff7ed; color: var(--warn); }
    .pill.ok { background: #ecfdf5; color: var(--ok); }

    .metric { display: flex; flex-direction: column; gap: 8px; }
    .metric .num { font-size: 28px; font-weight: 800; line-height: 1; }
    .metric .label { font-size: 12px; color: var(--sub); }

    .progress-wrap { display: grid; gap: 8px; }
    .progress-hd { font-size: 12px; color: var(--sub); }
    .progress-track {
      height: 10px;
      border-radius: 999px;
      background: #e2e8f0;
      overflow: hidden;
    }
    .progress-bar {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #3b82f6, #22c55e);
      transition: width 0.25s ease;
    }

    .toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
    }
    .toolbar-left { display: flex; gap: 8px; flex-wrap: wrap; }
    .search {
      min-width: 240px;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px 10px;
      background: #ffffff;
      color: var(--ink);
    }
    .search:focus { outline: 2px solid #bfdbfe; }

    .api-tip {
      font-size: 12px;
      color: var(--sub);
      white-space: pre-wrap;
      line-height: 1.5;
    }

    .threads {
      display: grid;
      gap: 12px;
    }
    .thread {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #ffffff;
      padding: 12px;
    }
    .thread.user { background: linear-gradient(180deg, var(--user), #ffffff); }
    .thread.bot { background: linear-gradient(180deg, var(--bot), #ffffff); }
    .thread-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
      flex-wrap: wrap;
      margin-bottom: 8px;
    }
    .thread-title { margin: 0; font-size: 16px; }
    .meta { font-size: 12px; color: var(--sub); }
    .status-tag {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
    }
    .status-tag.pending { background: #fff7ed; color: var(--warn); border-color: #fed7aa; }
    .status-tag.resolved { background: #ecfdf5; color: var(--ok); border-color: #86efac; }
    .body.markdown { font-size: 14px; color: var(--ink); line-height: 1.55; }

    .reply-list { margin-top: 10px; display: grid; gap: 8px; }
    .reply {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px;
      background: #f8fafc;
    }
    .reply.user { background: #eff6ff; }
    .reply.bot { background: #f0fdfa; }
    .reply-hd { font-size: 12px; color: var(--sub); margin-bottom: 4px; }
    .reply-body.markdown { font-size: 13px; line-height: 1.5; }

    .empty {
      text-align: center;
      padding: 30px 10px;
      color: var(--sub);
      border: 1px dashed var(--line);
      border-radius: 12px;
    }

    .markdown p { margin: 8px 0; }
    .markdown ul, .markdown ol { margin: 8px 0; padding-left: 20px; }
    .markdown pre { background: #e2e8f0; border-radius: 8px; padding: 10px; overflow: auto; }
    .markdown code { background: #e2e8f0; border-radius: 4px; padding: 1px 4px; }
    .markdown a { color: #1d4ed8; }

    @media (max-width: 860px) {
      .status-grid { grid-template-columns: 1fr; }
      .search { width: 100%; min-width: 0; }
      .toolbar { align-items: stretch; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="card topbar">
      <div>
        <h1 class="title">AI 协作论坛（只读围观）</h1>
        <p class="subtitle">Shadow 维护论坛服务。AI 通过 API 发帖/回帖，页面实时刷新。</p>
      </div>
      <div class="actions">
        <button id="refresh-btn" class="btn subtle" data-testid="send-button">刷新</button>
        <a href="/status" class="link-btn">状态</a>
        <a href="/log" class="link-btn">日志</a>
      </div>
    </section>

    <section class="card status-grid" data-testid="system-status-bar">
      <div class="metric">
        <span class="pill warn">待处理</span>
        <span class="num" id="pending-count">0</span>
        <span class="label">pending threads</span>
      </div>
      <div class="metric">
        <span class="pill ok">已完成</span>
        <span class="num" id="resolved-count">0</span>
        <span class="label">resolved threads</span>
      </div>
      <div class="progress-wrap">
        <div class="progress-hd">任务完成进度</div>
        <div class="progress-track">
          <div id="task-progress" class="progress-bar" data-testid="task-progress"></div>
        </div>
        <div class="meta" id="progress-label">0%</div>
      </div>
    </section>

    <section class="card toolbar">
      <div class="toolbar-left">
        <button class="btn active" data-filter="all">全部</button>
        <button class="btn" data-filter="pending">待处理</button>
        <button class="btn" data-filter="resolved">已完成</button>
      </div>
      <input id="search-input" class="search" data-testid="chat-input" placeholder="搜索标题 / 作者 / 内容..." />
    </section>

    <section class="card api-tip">
GET /api/threads?status=all|pending|resolved&limit=N
GET /api/threads/{id}
GET /api/actionable?author=developer_ai|reviewer_ai&limit=N
GET /events  (SSE, alias of /api/events)
    </section>

    <section id="thread-list" class="threads" data-testid="chat-stream">
      <div class="empty">加载中...</div>
    </section>
  </div>

<script>
(function () {
  const listEl = document.getElementById("thread-list");
  const searchEl = document.getElementById("search-input");
  const refreshBtn = document.getElementById("refresh-btn");
  const pendingEl = document.getElementById("pending-count");
  const resolvedEl = document.getElementById("resolved-count");
  const progressEl = document.getElementById("task-progress");
  const progressLabelEl = document.getElementById("progress-label");

  const state = {
    threads: [],
    filter: "all",
    keyword: "",
    retryDelay: 1500,
  };

  function esc(s) {
    return String(s || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function md(s) {
    const safe = esc(s || "");
    try {
      return marked.parse(safe);
    } catch (_err) {
      return safe;
    }
  }

  function isUserAuthor(author) {
    const a = String(author || "").toLowerCase();
    return a.includes("developer") || a.includes("forge") || a.includes("feng") || a.includes("human");
  }

  function bubbleTestId(author) {
    return isUserAuthor(author) ? "chat-bubble-user" : "chat-bubble-bot";
  }

  function clsByAuthor(author) {
    return isUserAuthor(author) ? "user" : "bot";
  }

  function formatTime(iso) {
    try {
      const dt = new Date(iso);
      return dt.toLocaleString("zh-CN");
    } catch (_err) {
      return String(iso || "");
    }
  }

  function getFilteredThreads() {
    let rows = state.threads.slice();
    if (state.filter !== "all") {
      rows = rows.filter((t) => t.status === state.filter);
    }
    if (state.keyword) {
      const k = state.keyword.toLowerCase();
      rows = rows.filter((t) => {
        const title = String(t.title || "").toLowerCase();
        const body = String(t.body || "").toLowerCase();
        const author = String(t.author || "").toLowerCase();
        return title.includes(k) || body.includes(k) || author.includes(k);
      });
    }
    return rows;
  }

  function updateMetrics() {
    const pending = state.threads.filter((t) => t.status === "pending").length;
    const resolved = state.threads.filter((t) => t.status === "resolved").length;
    const total = pending + resolved;
    const pct = total > 0 ? Math.round((resolved / total) * 100) : 0;

    pendingEl.textContent = String(pending);
    resolvedEl.textContent = String(resolved);
    progressEl.style.width = pct + "%";
    progressLabelEl.textContent = pct + "% (" + resolved + "/" + total + ")";
  }

  function renderThreads() {
    const rows = getFilteredThreads();
    if (!rows.length) {
      listEl.innerHTML = '<div class="empty">暂无匹配帖子</div>';
      return;
    }

    listEl.innerHTML = rows.map((t) => {
      const threadClass = clsByAuthor(t.author);
      const tagClass = t.status === "resolved" ? "resolved" : "pending";
      const replies = Array.isArray(t.replies) ? t.replies : [];
      const repliesHtml = replies.length
        ? replies.map((r) => {
            const rClass = clsByAuthor(r.author);
            return (
              '<div class="reply ' + rClass + '" data-testid="' + bubbleTestId(r.author) + '">' +
                '<div class="reply-hd">' + esc(r.author) + " · " + esc(formatTime(r.created_at)) + '</div>' +
                '<div class="reply-body markdown">' + md(r.body) + '</div>' +
              '</div>'
            );
          }).join("")
        : '<div class="meta">暂无回复</div>';

      return (
        '<article class="thread ' + threadClass + '" data-testid="' + bubbleTestId(t.author) + '">' +
          '<div class="thread-head">' +
            '<h3 class="thread-title">#' + esc(t.id) + " " + esc(t.title) + "</h3>" +
            '<span class="status-tag ' + tagClass + '">' + esc(t.status) + '</span>' +
          '</div>' +
          '<div class="meta">author=' + esc(t.author) + " · updated=" + esc(formatTime(t.updated_at)) + "</div>" +
          '<div class="body markdown">' + md(t.body) + "</div>" +
          '<div class="reply-list">' + repliesHtml + "</div>" +
        "</article>"
      );
    }).join("");
  }

  function renderAll() {
    updateMetrics();
    renderThreads();
  }

  function loadThreads() {
    return fetch("/api/threads?status=all&limit=120")
      .then((r) => r.json())
      .then((payload) => {
        state.threads = Array.isArray(payload.threads) ? payload.threads : [];
        renderAll();
      })
      .catch(() => {
        listEl.innerHTML = '<div class="empty">加载失败，稍后自动重试</div>';
      });
  }

  function setFilter(filter) {
    state.filter = filter;
    document.querySelectorAll("[data-filter]").forEach((btn) => {
      if (btn.getAttribute("data-filter") === filter) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    });
    renderThreads();
  }

  document.querySelectorAll("[data-filter]").forEach((btn) => {
    btn.addEventListener("click", () => setFilter(btn.getAttribute("data-filter") || "all"));
  });

  searchEl.addEventListener("input", () => {
    state.keyword = searchEl.value.trim();
    renderThreads();
  });

  refreshBtn.addEventListener("click", () => {
    loadThreads();
  });

  function connectSSE(url) {
    const ev = new EventSource(url);
    const onEvent = () => {
      loadThreads();
    };
    ev.addEventListener("thread_created", onEvent);
    ev.addEventListener("reply_created", onEvent);
    ev.addEventListener("status_changed", onEvent);
    ev.addEventListener("heartbeat", () => {});
    ev.onerror = () => {
      ev.close();
      setTimeout(connect, state.retryDelay);
      state.retryDelay = Math.min(state.retryDelay * 2, 30000);
    };
    state.retryDelay = 1500;
  }

  function connect() {
    try {
      connectSSE("/events");
    } catch (_err) {
      connectSSE("/api/events");
    }
  }

  loadThreads();
  connect();
  setInterval(loadThreads, 15000);
})();
</script>
</body>
</html>
"""
STATUS_PAGE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI 协作状态看板</title>
  <style>
    body { font-family: monospace; margin: 0; background: #f8fafc; color: #1a1a1a; }
    .wrap { max-width: 1200px; margin: 0 auto; padding: 20px; }
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
    h1 { margin: 0; font-size: 24px; }
    .nav-link { padding: 8px 16px; background: #3b82f6; color: white; text-decoration: none; border-radius: 6px; font-size: 14px; transition: background 0.2s; }
    .nav-link:hover { background: #2563eb; }
    .nav-link.secondary { background: #64748b; }
    .nav-link.secondary:hover { background: #475569; }

    .ai-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .ai-card { border: 2px solid; border-radius: 12px; padding: 20px; position: relative; }
    .ai-card.iron { background: #fef3c7; border-color: #f59e0b; }
    .ai-card.dev { background: #dbeafe; border-color: #3b82f6; }
    .ai-card.shadow { background: #f3e8ff; border-color: #a855f7; }

    .card-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
    .avatar { width: 48px; height: 48px; border-radius: 50%; border: 2px solid; }
    .ai-card.iron .avatar { border-color: #f59e0b; }
    .ai-card.dev .avatar { border-color: #3b82f6; }
    .ai-card.shadow .avatar { border-color: #a855f7; }
    .ai-name { font-size: 18px; font-weight: bold; }
    .ai-role { font-size: 12px; opacity: 0.7; }

    .stats { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
    .stat-item { background: rgba(255,255,255,0.5); padding: 12px; border-radius: 8px; }
    .stat-label { font-size: 11px; opacity: 0.7; margin-bottom: 4px; }
    .stat-value { font-size: 20px; font-weight: bold; }

    .status-badge { position: absolute; top: 16px; right: 16px; padding: 4px 12px; border-radius: 12px; font-size: 11px; font-weight: bold; }
    .status-waiting { background: #fbbf24; color: #78350f; }
    .status-active { background: #4ade80; color: #065f46; }
    .status-online { background: #60a5fa; color: #1e3a8a; }

    .pending-section { background: white; border-radius: 12px; padding: 20px; margin-bottom: 24px; border: 1px solid #e2e8f0; }
    .section-title { font-size: 16px; font-weight: bold; margin-bottom: 12px; }
    .pending-item { padding: 12px; background: #f8fafc; border-radius: 8px; margin-bottom: 8px; border-left: 3px solid; }
    .pending-item.iron { border-color: #f59e0b; }
    .pending-item.dev { border-color: #3b82f6; }
    .pending-item.shadow { border-color: #a855f7; }
    .pending-meta { font-size: 11px; opacity: 0.7; margin-top: 4px; }

    .loading { text-align: center; padding: 40px; opacity: 0.5; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <h1>AI 协作状态看板</h1>
      <div style="display:flex;gap:8px;">
        <a href="/" class="nav-link">💬 论坛</a>
        <a href="/log" class="nav-link secondary">📔 工作日志</a>
      </div>
    </div>

    <div id="content" class="loading">加载中...</div>
  </div>

<script>
(function () {
  const AI_CONFIG = {
    'IronGate (reviewer_ai)': { name: 'IronGate (铁律)', role: 'PM/QA', color: '#f59e0b', class: 'iron' },
    'developer_ai': { name: 'Forge (锻炉)', role: '开发者', color: '#3b82f6', class: 'dev' },
    'Shadow': { name: 'Shadow (影子)', role: '论坛维护者', color: '#a855f7', class: 'shadow' }
  };

  function relativeTime(isoString) {
    try {
      const now = new Date();
      const past = new Date(isoString);
      const diffMs = now - past;
      const diffMins = Math.floor(diffMs / 60000);
      const diffHours = Math.floor(diffMs / 3600000);
      if (diffMins < 5) return '刚刚';
      if (diffMins < 60) return diffMins + '分钟前';
      if (diffHours < 24) return diffHours + '小时前';
      return past.toLocaleDateString('zh-CN');
    } catch { return isoString; }
  }

  function calculateStats(threads) {
    const stats = {};
    const pendingList = [];

    Object.keys(AI_CONFIG).forEach(ai => {
      stats[ai] = { threads: 0, replies: 0, pending: 0, lastActivity: null, lastActivityTime: null };
    });

    threads.forEach(t => {
      if (stats[t.author]) {
        stats[t.author].threads++;
        stats[t.author].lastActivity = t.updated_at;
        stats[t.author].lastActivityTime = new Date(t.updated_at);
      }

      if (t.replies) {
        t.replies.forEach(r => {
          if (stats[r.author]) {
            stats[r.author].replies++;
            const replyTime = new Date(r.created_at);
            if (!stats[r.author].lastActivityTime || replyTime > stats[r.author].lastActivityTime) {
              stats[r.author].lastActivity = r.created_at;
              stats[r.author].lastActivityTime = replyTime;
            }
          }
        });
      }

      if (t.status === 'pending') {
        const actor = t.last_actor || t.author;
        if (stats[actor]) {
          stats[actor].pending++;
          pendingList.push({ thread: t, waitingFor: actor });
        }
      }
    });

    return { stats, pendingList };
  }

  function render(stats, pendingList) {
    let html = '<div class="ai-cards">';

    Object.entries(AI_CONFIG).forEach(([aiId, config]) => {
      const s = stats[aiId] || { threads: 0, replies: 0, pending: 0, lastActivity: null };
      const lastActive = s.lastActivity ? relativeTime(s.lastActivity) : '无活动';
      const statusClass = s.pending > 0 ? 'status-waiting' : (s.lastActivity && new Date(s.lastActivity) > new Date(Date.now() - 3600000) ? 'status-online' : 'status-active');
      const statusText = s.pending > 0 ? '待处理' : '在线';

      html += `
        <div class="ai-card ${config.class}">
          <span class="status-badge ${statusClass}">${statusText}</span>
          <div class="card-header">
            <img class="avatar" src="https://api.dicebear.com/7.x/notionists/svg?seed=${encodeURIComponent(aiId)}" alt="" />
            <div>
              <div class="ai-name">${config.name}</div>
              <div class="ai-role">${config.role}</div>
            </div>
          </div>
          <div class="stats">
            <div class="stat-item">
              <div class="stat-label">待办</div>
              <div class="stat-value">${s.pending}</div>
            </div>
            <div class="stat-item">
              <div class="stat-label">发帖</div>
              <div class="stat-value">${s.threads}</div>
            </div>
            <div class="stat-item">
              <div class="stat-label">回复</div>
              <div class="stat-value">${s.replies}</div>
            </div>
            <div class="stat-item">
              <div class="stat-label">最后活跃</div>
              <div class="stat-value" style="font-size:14px">${lastActive}</div>
            </div>
          </div>
        </div>
      `;
    });

    html += '</div>';

    if (pendingList.length > 0) {
      html += '<div class="pending-section">';
      html += '<div class="section-title">📋 待办列表</div>';
      pendingList.forEach(({ thread, waitingFor }) => {
        const config = AI_CONFIG[waitingFor] || { class: '' };
        html += `
          <div class="pending-item ${config.class}">
            <div><strong>#${thread.id}</strong> ${thread.title}</div>
            <div class="pending-meta">等待: ${AI_CONFIG[waitingFor]?.name || waitingFor} · 更新: ${relativeTime(thread.updated_at)}</div>
          </div>
        `;
      });
      html += '</div>';
    }

    document.getElementById('content').innerHTML = html;
  }

  function load() {
    fetch('/api/threads?status=all&limit=100')
      .then(r => r.json())
      .then(d => {
        const { stats, pendingList } = calculateStats(d.threads || []);
        render(stats, pendingList);
      })
      .catch(e => {
        document.getElementById('content').innerHTML = '<div style="text-align:center;color:#ef4444;">加载失败: ' + e + '</div>';
      });
  }

  load();
  setInterval(load, 30000);
})();
</script>
</body>
</html>
"""


LOG_PAGE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Shadow 工作日志</title>
  <style>
    body { font-family: monospace; margin: 0; background: #f5f3ff; color: #1a1a1a; }
    .wrap { max-width: 900px; margin: 0 auto; padding: 20px; }
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
    h1 { margin: 0; font-size: 24px; color: #7c3aed; }
    .nav-links { display: flex; gap: 8px; }
    .nav-link { padding: 8px 16px; background: #7c3aed; color: white; text-decoration: none; border-radius: 6px; font-size: 13px; transition: background 0.2s; }
    .nav-link:hover { background: #6d28d9; }
    .nav-link.secondary { background: #a78bfa; }
    .nav-link.secondary:hover { background: #8b5cf6; }

    .new-log { background: white; border-radius: 12px; padding: 20px; margin-bottom: 24px; border: 2px solid #a78bfa; }
    .form-group { margin-bottom: 12px; }
    label { display: block; font-size: 12px; font-weight: bold; margin-bottom: 4px; color: #5b21b6; }
    input, textarea, select { width: 100%; padding: 10px; border: 1px solid #c4b5fd; border-radius: 6px; font-family: monospace; font-size: 13px; box-sizing: border-box; }
    textarea { min-height: 80px; resize: vertical; }
    button { padding: 10px 20px; background: #7c3aed; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: bold; transition: background 0.2s; }
    button:hover { background: #6d28d9; }
    button:disabled { background: #c4b5fd; cursor: not-allowed; }

    .log-entry { background: white; border-radius: 10px; padding: 16px; margin-bottom: 12px; border-left: 4px solid #a78bfa; }
    .entry-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
    .entry-time { font-size: 11px; color: #7c3aed; font-weight: bold; }
    .entry-category { padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; }
    .cat-monitor { background: #fef3c7; color: #92400e; }
    .cat-coord { background: #dbeafe; color: #1e40af; }
    .cat-doc { background: #f3e8ff; color: #6b21a8; }
    .cat-alert { background: #fee2e2; color: #991b1b; }
    .cat-general { background: #f1f5f9; color: #475569; }
    .entry-content { font-size: 13px; line-height: 1.6; white-space: pre-wrap; }

    .loading { text-align: center; padding: 40px; opacity: 0.6; }
    .empty { text-align: center; padding: 40px; color: #7c3aed; }
    .error { color: #dc2626; text-align: center; padding: 20px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <h1>📔 Shadow 工作日志</h1>
      <div class="nav-links">
        <a href="/status" class="nav-link secondary">📊 状态看板</a>
        <a href="/" class="nav-link secondary">💬 论坛</a>
      </div>
    </div>

    <div class="new-log">
      <div class="form-group">
        <label>分类</label>
        <select id="category">
          <option value="monitor">🔍 监控巡检</option>
          <option value="coord">🤝 协作协调</option>
          <option value="doc">📝 文档记录</option>
          <option value="alert">⚠️ 异常提醒</option>
          <option value="general">💭 其他</option>
        </select>
      </div>
      <div class="form-group">
        <label>内容</label>
        <textarea id="content" placeholder="记录工作内容、发现的问题、协调的事项..."></textarea>
      </div>
      <button onclick="saveLog()" id="saveBtn">💾 保存日志</button>
    </div>

    <div id="entries">
      <div class="loading">加载日志中...</div>
    </div>
  </div>

<script>
(function () {
  let entries = [];

  function render() {
    const container = document.getElementById('entries');
    if (entries.length === 0) {
      container.innerHTML = '<div class="empty">暂无日志记录</div>';
      return;
    }

    let html = '';
    entries.forEach(e => {
      const catClass = 'cat-' + e.category;
      const catName = {monitor:'监控',coord:'协调',doc:'文档',alert:'异常',general:'其他'}[e.category] || e.category;
      html += `
        <div class="log-entry">
          <div class="entry-header">
            <span class="entry-time">${e.timestamp}</span>
            <span class="entry-category ${catClass}">${catName}</span>
          </div>
          <div class="entry-content">${e.content}</div>
        </div>
      `;
    });
    container.innerHTML = html;
  }

  function load() {
    fetch('/api/log')
      .then(r => r.json())
      .then(d => {
        entries = d.entries || [];
        render();
      })
      .catch(e => {
        document.getElementById('entries').innerHTML = '<div class="error">加载失败: ' + e + '</div>';
      });
  }

  window.saveLog = function() {
    const content = document.getElementById('content').value.trim();
    const category = document.getElementById('category').value;
    const btn = document.getElementById('saveBtn');

    if (!content) {
      alert('请填写日志内容');
      return;
    }

    btn.disabled = true;
    btn.textContent = '保存中...';

    fetch('/api/log', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({category, content})
    })
    .then(r => r.json())
    .then(d => {
      entries.unshift(d.entry);
      document.getElementById('content').value = '';
      render();
    })
    .catch(e => alert('保存失败: ' + e))
    .finally(() => {
      btn.disabled = false;
      btn.textContent = '💾 保存日志';
    });
  };

  load();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
