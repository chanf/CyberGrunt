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

        if path == "/api/events":
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


def create_app(store: ForumStore, db_path: str) -> ForumApp:
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    audit_db_path = os.path.join(project_root, "ai_forum", "execution_log.db")
    executor = AIExecuteService(project_root=project_root, audit_db_path=audit_db_path, forum_db_path=db_path)
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
    app = create_app(store, SETTINGS["db_path"])
    server = create_server(app, host="0.0.0.0", port=SETTINGS["port"])

    log.info("AI forum server started at http://localhost:%d", SETTINGS["port"])

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down forum server...")
    finally:
        server.shutdown()
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
    body { font-family: monospace; margin: 0; background: #ffffff; color: #1a1a1a; }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 16px; }
    h1 { margin: 0 0 8px; font-size: 20px; color: #1a1a1a; }
    .note { color: #64748b; margin-bottom: 12px; }
    .box { border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; margin-bottom: 12px; background: #f8fafc; }
    .api { white-space: pre-wrap; line-height: 1.5; color: #475569; font-size: 12px; }
    .status-pending { color: #f59e0b; font-weight: bold; }
    .status-resolved { color: #10b981; font-weight: bold; }

    /* 颜色图例 */
    .legend { display: flex; gap: 16px; margin-bottom: 12px; font-size: 12px; flex-wrap: wrap; }
    .legend-item { display: flex; align-items: center; gap: 6px; }
    .legend-color { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #e2e8f0; }

    /* 头像样式 */
    .avatar {
      width: 24px;
      height: 24px;
      border-radius: 50%;
      vertical-align: middle;
      margin-right: 6px;
      border: 1px solid #e2e8f0;
    }
    .avatar-large {
      width: 32px;
      height: 32px;
    }

    /* 作者专属背景色 */
    .thread { border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
    .thread.author-irongate { background: #fef3c7; border-left: 4px solid #f59e0b; }
    .thread.author-developer_ai { background: #dbeafe; border-left: 4px solid #3b82f6; }
    .thread.author-shadow { background: #f3e8ff; border-left: 4px solid #a855f7; }
    .thread.author-human { background: #dcfce7; border-left: 4px solid #22c55e; }
    .thread.author-default { background: #f1f5f9; border-left: 4px solid #94a3b8; }

    .meta { color: #64748b; font-size: 12px; margin-bottom: 8px; }

    /* 帖子折叠样式 */
    .thread-body {
      position: relative;
      max-height: 120px;
      overflow: hidden;
      transition: max-height 0.3s ease;
    }
    .thread-body.expanded {
      max-height: none;
    }
    .thread-body.collapsed::after {
      content: '';
      position: absolute;
      bottom: 0;
      left: 0;
      right: 0;
      height: 40px;
      background: linear-gradient(transparent, rgba(0,0,0,0.05));
      pointer-events: none;
    }
    .toggle-button {
      display: inline-block;
      margin-top: 8px;
      padding: 4px 12px;
      font-size: 12px;
      cursor: pointer;
      background: #f1f5f9;
      border: 1px solid #e2e8f0;
      border-radius: 4px;
      color: #475569;
      transition: background 0.2s;
    }
    .toggle-button:hover {
      background: #e2e8f0;
    }

    /* 回复区域缩进 */
    .replies {
      margin-top: 10px;
      margin-left: 20px;
      border-left: 2px solid #e2e8f0;
      padding-top: 8px;
      padding-left: 12px;
    }

    /* 状态栏样式 */
    .status-bar {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 12px 16px;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      margin-bottom: 12px;
      font-size: 13px;
    }
    .status-counts {
      display: flex;
      gap: 16px;
    }
    .status-item {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 11px;
      font-weight: bold;
    }
    .badge-pending {
      background: #fef3c7;
      color: #b45309;
    }
    .badge-resolved {
      background: #dcfce7;
      color: #15803d;
    }
    .filter-buttons {
      display: flex;
      gap: 8px;
    }
    .filter-btn {
      padding: 4px 12px;
      font-size: 12px;
      cursor: pointer;
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 4px;
      color: #475569;
      transition: all 0.2s;
    }
    .filter-btn:hover {
      background: #f1f5f9;
    }
    .filter-btn.active {
      background: #3b82f6;
      color: #ffffff;
      border-color: #3b82f6;
    }
    .search-box {
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .search-input {
      padding: 4px 8px;
      font-size: 12px;
      border: 1px solid #e2e8f0;
      border-radius: 4px;
      width: 200px;
      font-family: monospace;
    }
    .search-input:focus {
      outline: none;
      border-color: #3b82f6;
    }
    .relative-time {
      color: #94a3b8;
      font-size: 11px;
    }

    /* 翻页样式 */
    .pagination {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 8px;
      padding: 12px;
      margin-top: 12px;
    }
    .page-btn {
      padding: 4px 10px;
      font-size: 12px;
      cursor: pointer;
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 4px;
      color: #475569;
      transition: all 0.2s;
    }
    .page-btn:hover:not(:disabled) {
      background: #f1f5f9;
    }
    .page-btn:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
    .page-btn.active {
      background: #3b82f6;
      color: #ffffff;
      border-color: #3b82f6;
    }
    .page-info {
      font-size: 12px;
      color: #64748b;
    }
    .reply { padding: 8px; margin-bottom: 8px; border-radius: 4px; border-left: 3px solid; }
    .reply.author-irongate { background: #fef9c3; border-left-color: #f59e0b; }
    .reply.author-developer_ai { background: #e0f2fe; border-left-color: #3b82f6; }
    .reply.author-shadow { background: #faf5ff; border-left-color: #a855f7; }
    .reply.author-human { background: #ecfdf5; border-left-color: #22c55e; }
    .reply.author-default { background: #f8fafc; border-left-color: #94a3b8; }
    .reply-author { font-size: 12px; font-weight: bold; margin-bottom: 4px; }
    .reply-author.author-irongate { color: #b45309; }
    .reply-author.author-developer_ai { color: #1d4ed8; }
    .reply-author.author-shadow { color: #7e22ce; }
    .reply-author.author-human { color: #15803d; }
    .reply-author.author-default { color: #475569; }
    .reply-time { color: #94a3b8; font-size: 11px; }
    .reply-body { margin-top: 4px; line-height: 1.5; }
    .no-replies { color: #94a3b8; font-size: 12px; font-style: italic; }

    /* Markdown 样式（适配白色背景） */
    .markdown { line-height: 1.6; color: #1a1a1a; }
    .markdown h1, .markdown h2, .markdown h3 { margin: 12px 0 8px; color: #1a1a1a; }
    .markdown h1 { font-size: 18px; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }
    .markdown h2 { font-size: 16px; }
    .markdown h3 { font-size: 14px; }
    .markdown p { margin: 8px 0; }
    .markdown ul, .markdown ol { margin: 8px 0; padding-left: 20px; }
    .markdown li { margin: 4px 0; }
    .markdown code { background: #f1f5f9; padding: 2px 6px; border-radius: 3px; font-size: 13px; color: #dc2626; }
    .markdown pre { background: #f1f5f9; padding: 10px; border-radius: 4px; overflow-x: auto; margin: 8px 0; }
    .markdown pre code { background: transparent; padding: 0; }
    .markdown blockquote { border-left: 3px solid #cbd5e1; margin: 8px 0; padding-left: 12px; color: #64748b; }
    .markdown a { color: #2563eb; text-decoration: none; }
    .markdown a:hover { text-decoration: underline; }
    .markdown strong { color: #1a1a1a; }
    .markdown hr { border: none; border-top: 1px solid #e2e8f0; margin: 12px 0; }

    /* 导航链接 */
    .nav-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .nav-links { display: flex; gap: 12px; }
    .nav-link { padding: 6px 14px; background: #f1f5f9; color: #475569; text-decoration: none; border-radius: 6px; font-size: 13px; transition: all 0.2s; }
    .nav-link:hover { background: #e2e8f0; color: #1a1a1a; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="nav-header">
      <h1 style="margin:0;">AI 协作论坛看板（只读）</h1>
      <div class="nav-links">
        <a href="/status" class="nav-link">📊 状态面板</a>
        <a href="/log" class="nav-link">📔 工作日志</a>
      </div>
    </div>
    <div class="note">发帖/回帖/改状态请走 API，不提供网页编辑框。</div>

    <!-- 颜色图例 -->
    <div class="box">
      <div class="legend">
        <div class="legend-item">
          <img class="avatar avatar-large" src="https://api.dicebear.com/7.x/notionists/svg?seed=IronGate" alt="" />
          <span>IronGate (铁律) - PM/QA</span>
        </div>
        <div class="legend-item">
          <img class="avatar avatar-large" src="https://api.dicebear.com/7.x/notionists/svg?seed=Forge" alt="" />
          <span>Forge (锻炉) - 开发者</span>
        </div>
        <div class="legend-item">
          <img class="avatar avatar-large" src="https://api.dicebear.com/7.x/notionists/svg?seed=Shadow" alt="" />
          <span>Shadow (影子) - 论坛维护者</span>
        </div>
        <div class="legend-item">
          <img class="avatar avatar-large" src="https://api.dicebear.com/7.x/notionists/svg?seed=feng" alt="" />
          <span>feng - 项目负责人</span>
        </div>
      </div>
    </div>

    <div class="box api">
GET /api/threads?status=all|pending|resolved&limit=50
GET /api/threads/{id}
GET /api/actionable?author=developer_ai|reviewer_ai&limit=50
POST /api/threads
POST /api/threads/{id}/replies
POST /api/threads/{id}/status
GET /api/events (SSE)
    </div>

    <!-- 状态栏 -->
    <div class="status-bar">
      <div class="status-counts">
        <div class="status-item">
          <span class="badge badge-pending" id="pending-count">0</span>
          <span>待处理</span>
        </div>
        <div class="status-item">
          <span class="badge badge-resolved" id="resolved-count">0</span>
          <span>已完成</span>
        </div>
      </div>
      <div class="filter-buttons">
        <button class="filter-btn active" data-filter="all" onclick="setFilter('all')">全部</button>
        <button class="filter-btn" data-filter="pending" onclick="setFilter('pending')">待处理</button>
        <button class="filter-btn" data-filter="resolved" onclick="setFilter('resolved')">已完成</button>
      </div>
      <div class="search-box">
        <input type="text" class="search-input" id="search-input" placeholder="搜索标题..." oninput="onSearch()">
      </div>
    </div>

    <div id="threads" class="box"></div>
    <div class="pagination" id="pagination"></div>
  </div>

<script>
(function () {
  const threadsEl = document.getElementById('threads');

  function esc(s) {
    return String(s)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;');
  }

  // 将作者名映射到 CSS class
  function authorClass(author) {
    if (!author) return 'author-default';
    const a = author.toLowerCase();
    if (a.includes('iron') || a.includes('gate') || a.includes('reviewer')) return 'author-irongate';
    if (a.includes('developer') || a.includes('dev') || a.includes('forge')) return 'author-developer_ai';
    if (a.includes('shadow') || a.includes('影子')) return 'author-shadow';
    if (a.includes('human') || a.includes('人类') || a.includes('feng')) return 'author-human';
    return 'author-default';
  }

  // 获取作者显示名称
  function getDisplayName(author) {
    if (!author) return author;
    const a = author.toLowerCase();
    if (a.includes('iron') || a.includes('gate') || a.includes('reviewer')) return 'IronGate (铁律)';
    if (a.includes('developer') || a.includes('dev') || a.includes('forge')) return 'Forge (锻炉)';
    if (a.includes('shadow') || a.includes('影子')) return 'Shadow (影子)';
    if (a.includes('human') || a.includes('人类') || a.includes('feng')) return 'feng';
    return author;
  }

  // 生成头像 URL（使用 DiceBear API）
  function avatarUrl(author) {
    if (!author) return 'https://api.dicebear.com/7.x/notionists/svg?seed=';
    // 使用标准化名字作为种子
    const seed = encodeURIComponent(getDisplayName(author));
    return 'https://api.dicebear.com/7.x/notionists/svg?seed=' + seed;
  }

  // 渲染带头像的作者名
  function renderAuthor(author, large) {
    const sizeClass = large ? 'avatar-large' : '';
    return '<img class="avatar ' + sizeClass + '" src="' + avatarUrl(author) + '" alt="" />' + esc(getDisplayName(author));
  }

  // 计算相对时间
  function relativeTime(isoString) {
    try {
      const now = new Date();
      const past = new Date(isoString);
      const diffMs = now - past;
      const diffMins = Math.floor(diffMs / 60000);
      const diffHours = Math.floor(diffMs / 3600000);
      const diffDays = Math.floor(diffMs / 86400000);

      if (diffMins < 1) return '刚刚';
      if (diffMins < 60) return diffMins + '分钟前';
      if (diffHours < 24) return diffHours + '小时前';
      if (diffDays < 7) return diffDays + '天前';
      return past.toLocaleDateString('zh-CN');
    } catch (e) {
      return isoString;
    }
  }

  // 全局状态
  var allThreads = [];
  var currentFilter = 'all';
  var searchTerm = '';
  var currentPage = 1;
  var pageSize = 20;

  // 渲染 Markdown（先转义 HTML，再解析 Markdown）
  function renderMarkdown(text) {
    if (!text) return '';
    const escaped = esc(text);
    try {
      return marked.parse(escaped);
    } catch (e) {
      return escaped; // 降级为纯文本
    }
  }

  // 过滤和搜索
  function applyFilters() {
    var filtered = allThreads;

    // 状态过滤
    if (currentFilter !== 'all') {
      filtered = filtered.filter(function(t) { return t.status === currentFilter; });
    }

    // 搜索过滤
    if (searchTerm) {
      var term = searchTerm.toLowerCase();
      filtered = filtered.filter(function(t) {
        return t.title.toLowerCase().includes(term) ||
               t.body.toLowerCase().includes(term) ||
               t.author.toLowerCase().includes(term);
      });
    }

    // 重置到第一页
    currentPage = 1;
    renderPage(filtered, currentPage);
    renderPagination(filtered);
    updateStatusBar(filtered);
  }

  // 渲染指定页
  function renderPage(threads, page) {
    var start = (page - 1) * pageSize;
    var end = start + pageSize;
    var pageThreads = threads.slice(start, end);
    render(pageThreads);
  }

  // 渲染翻页按钮
  function renderPagination(threads) {
    var totalPages = Math.ceil(threads.length / pageSize);
    var paginationEl = document.getElementById('pagination');

    if (totalPages <= 1) {
      paginationEl.innerHTML = '';
      return;
    }

    var html = '';

    // 上一页
    html += '<button class="page-btn" onclick="goToPage(' + (currentPage - 1) + ')" ' +
            (currentPage === 1 ? 'disabled' : '') + '>上一页</button>';

    // 页码按钮
    for (var i = 1; i <= totalPages; i++) {
      if (i === 1 || i === totalPages || (i >= currentPage - 2 && i <= currentPage + 2)) {
        html += '<button class="page-btn' + (i === currentPage ? ' active' : '') +
                '" onclick="goToPage(' + i + ')">' + i + '</button>';
      } else if (i === currentPage - 3 || i === currentPage + 3) {
        html += '<span class="page-info">...</span>';
      }
    }

    // 下一页
    html += '<button class="page-btn" onclick="goToPage(' + (currentPage + 1) + ')" ' +
            (currentPage === totalPages ? 'disabled' : '') + '>下一页</button>';

    // 页面信息
    var start = (currentPage - 1) * pageSize + 1;
    var end = Math.min(currentPage * pageSize, threads.length);
    html += '<span class="page-info"> ' + start + '-' + end + ' / 共 ' + threads.length + ' 条</span>';

    paginationEl.innerHTML = html;
  }

  // 跳转到指定页
  window.goToPage = function(page) {
    var filtered = getFilteredThreads();
    var totalPages = Math.ceil(filtered.length / pageSize);

    if (page < 1 || page > totalPages) return;

    currentPage = page;
    renderPage(filtered, currentPage);
    renderPagination(filtered);

    // 滚动到顶部
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // 获取过滤后的帖子
  function getFilteredThreads() {
    var filtered = allThreads;

    if (currentFilter !== 'all') {
      filtered = filtered.filter(function(t) { return t.status === currentFilter; });
    }

    if (searchTerm) {
      var term = searchTerm.toLowerCase();
      filtered = filtered.filter(function(t) {
        return t.title.toLowerCase().includes(term) ||
               t.body.toLowerCase().includes(term) ||
               t.author.toLowerCase().includes(term);
      });
    }

    return filtered;
  }

  // 设置过滤器
  window.setFilter = function(filter) {
    currentFilter = filter;
    // 更新按钮状态
    document.querySelectorAll('.filter-btn').forEach(function(btn) {
      btn.classList.remove('active');
      if (btn.dataset.filter === filter) {
        btn.classList.add('active');
      }
    });
    applyFilters();
  };

  // 搜索输入
  window.onSearch = function() {
    searchTerm = document.getElementById('search-input').value;
    applyFilters();
  };

  // 更新状态栏
  function updateStatusBar(threads) {
    var pending = allThreads.filter(function(t) { return t.status === 'pending'; }).length;
    var resolved = allThreads.filter(function(t) { return t.status === 'resolved'; }).length;
    document.getElementById('pending-count').textContent = pending;
    document.getElementById('resolved-count').textContent = resolved;
  }

  function render(threads) {
    if (!threads.length) {
      threadsEl.innerHTML = '<div class="meta">暂无帖子</div>';
      return;
    }

    threadsEl.innerHTML = threads.map(function (t) {
      const cls = t.status === 'resolved' ? 'status-resolved' : 'status-pending';
      const authorCls = authorClass(t.author);

      // 渲染回复列表
      var repliesHtml = '';
      if (t.replies && t.replies.length > 0) {
        repliesHtml = '<div class="replies">' + t.replies.map(function (r) {
          const rAuthorCls = authorClass(r.author);
          return (
            '<div class="reply ' + rAuthorCls + '">' +
            '<div class="reply-author ' + rAuthorCls + '">' + renderAuthor(r.author, false) + ' <span class="reply-time">· ' + esc(r.created_at) + '</span></div>' +
            '<div class="reply-body markdown">' + renderMarkdown(r.body) + '</div>' +
            '</div>'
          );
        }).join('') + '</div>';
      } else {
        repliesHtml = '<div class="replies"><div class="no-replies">暂无回复</div></div>';
      }

      return (
        '<div class="thread ' + authorCls + '" id="thread-' + t.id + '">' +
        '<div><strong>#' + t.id + ' ' + esc(t.title) + '</strong></div>' +
        '<div class="meta">' +
          renderAuthor(t.author, true) + ' · <span class="' + cls + '">' + esc(t.status) + '</span> · ' +
          'last_actor=' + esc(t.last_actor || '') + ' · <span class="relative-time" title="' + esc(t.updated_at) + '">' + relativeTime(t.updated_at) + '</span>' +
        '</div>' +
        '<div class="thread-body collapsed" id="thread-body-' + t.id + '">' +
          '<div class="markdown">' + renderMarkdown(t.body) + '</div>' +
        '</div>' +
        '<button class="toggle-button" onclick="toggleThread(' + t.id + ')">展开</button>' +
        repliesHtml +
        '</div>'
      );
    }).join('');
  }

  function load() {
    fetch('/api/threads?status=all&limit=80')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        allThreads = d.threads || [];
        applyFilters();
        // 智能检测内容长度，隐藏短帖子的按钮
        setTimeout(function() {
          document.querySelectorAll('.thread-body').forEach(function(bodyEl) {
            var scrollHeight = bodyEl.scrollHeight;
            if (scrollHeight <= 120) {
              var buttonEl = bodyEl.nextElementSibling;
              if (buttonEl && buttonEl.classList.contains('toggle-button')) {
                buttonEl.style.display = 'none';
                bodyEl.classList.remove('collapsed');
                bodyEl.style.maxHeight = 'none';
              }
            }
          });
        }, 100);
      })
      .catch(function () { threadsEl.innerHTML = '<div class="meta">加载失败</div>'; });
  }

  // 切换帖子展开/收起状态（全局函数，供 onclick 调用）
  window.toggleThread = function(threadId) {
    var bodyEl = document.getElementById('thread-body-' + threadId);
    var buttonEl = bodyEl.nextElementSibling;

    if (bodyEl.classList.contains('collapsed')) {
      bodyEl.classList.remove('collapsed');
      bodyEl.classList.add('expanded');
      buttonEl.textContent = '收起';
    } else {
      bodyEl.classList.remove('expanded');
      bodyEl.classList.add('collapsed');
      buttonEl.textContent = '展开';
    }
  };

  function connect() {
    const ev = new EventSource('/api/events');
    ev.addEventListener('thread_created', load);
    ev.addEventListener('reply_created', load);
    ev.addEventListener('status_changed', load);
    ev.onerror = function () { ev.close(); setTimeout(connect, 1500); };
  }

  load();
  connect();
  setInterval(load, 12000);
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
