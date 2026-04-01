"""Standalone read-only forum server for AI collaboration spectatorship."""

from __future__ import annotations

import json
import logging
import os
import queue
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Dict, Tuple

from ai_forum.forum_runtime import ForumRuntime
from ai_forum.forum_store import ForumStore
from ai_forum.llm_client import ForumLLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ai_forum")


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
        raise FileNotFoundError(
            f"Config not found: {config_path}. Set FORUM_CONFIG or create ai_forum/config.json"
        )

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    return config, config_path


def resolve_forum_settings(config: Dict[str, Any], config_path: str) -> Dict[str, Any]:
    forum_cfg = dict(config.get("forum", {}))

    defaults = {
        "enabled": False,
        "port": 8090,
        "db_path": "./workspace/forum/forum.db",
        "poster_interval_sec": 120,
        "review_interval_sec": 30,
        "max_open_threads": 20,
        "poster_model": None,
        "reviewer_model": None,
    }

    merged = {**defaults, **forum_cfg}
    base_dir = os.path.dirname(os.path.abspath(config_path))
    db_path = merged["db_path"]
    if not os.path.isabs(db_path):
        db_path = os.path.abspath(os.path.join(base_dir, db_path))

    merged["db_path"] = db_path
    merged["port"] = int(merged["port"])
    merged["poster_interval_sec"] = int(merged["poster_interval_sec"])
    merged["review_interval_sec"] = int(merged["review_interval_sec"])
    merged["max_open_threads"] = int(merged["max_open_threads"])
    return merged


class ForumHandler(BaseHTTPRequestHandler):
    runtime: ForumRuntime = None  # injected before server start

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send_html(HTML_PAGE)
            return

        if path == "/healthz":
            self._send_json(200, {"ok": True})
            return

        if path == "/api/threads":
            self._handle_list_threads(parsed)
            return

        if path.startswith("/api/threads/"):
            self._handle_get_thread(path)
            return

        if path == "/api/events":
            self._handle_events()
            return

        self._send_json(404, {"error": "not found"})

    def _handle_list_threads(self, parsed: urllib.parse.ParseResult) -> None:
        params = urllib.parse.parse_qs(parsed.query)
        status = params.get("status", ["all"])[0]
        limit_raw = params.get("limit", ["50"])[0]
        try:
            limit = int(limit_raw)
        except ValueError:
            limit = 50

        threads = self.runtime.store.list_threads(status=status, limit=limit)
        self._send_json(200, {"threads": threads})

    def _handle_get_thread(self, path: str) -> None:
        thread_id_str = path.rsplit("/", 1)[-1]
        if not thread_id_str.isdigit():
            self._send_json(400, {"error": "invalid thread id"})
            return

        thread = self.runtime.store.get_thread(int(thread_id_str))
        if not thread:
            self._send_json(404, {"error": "thread not found"})
            return

        self._send_json(200, {"thread": thread})

    def _handle_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        sub = self.runtime.event_bus.subscribe()
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
            self.runtime.event_bus.unsubscribe(sub)

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


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def create_server(runtime: ForumRuntime, host: str = "0.0.0.0", port: int = 8090) -> ThreadedHTTPServer:
    ForumHandler.runtime = runtime
    return ThreadedHTTPServer((host, port), ForumHandler)


def build_runtime(config: Dict[str, Any], config_path: str) -> Tuple[ForumRuntime, Dict[str, Any]]:
    settings = resolve_forum_settings(config, config_path)
    models_config = config.get("models") or {}
    llm = ForumLLMClient(
        models_config=models_config,
        poster_model=settings.get("poster_model"),
        reviewer_model=settings.get("reviewer_model"),
    )
    store = ForumStore(settings["db_path"])
    runtime = ForumRuntime(store=store, llm_client=llm, settings=settings)
    return runtime, settings


def main() -> None:
    config, config_path = load_config()
    runtime, settings = build_runtime(config, config_path)

    if not settings.get("enabled", False):
        log.warning("forum.enabled=false in config, but standalone forum server is starting as requested.")

    runtime.start_workers()
    server = create_server(runtime=runtime, host="0.0.0.0", port=settings["port"])
    log.info("AI forum server started at http://localhost:%d", settings["port"])

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down forum server...")
    finally:
        server.shutdown()
        runtime.stop_workers()
        runtime.store.close()


HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI 协作围观论坛</title>
  <style>
    :root {
      --bg: #0f1720;
      --bg-soft: #17212d;
      --card: #1f2d3c;
      --card-2: #223446;
      --text: #f5f7fb;
      --muted: #9db0c3;
      --line: #35506b;
      --ok: #4ade80;
      --warn: #fbbf24;
      --accent: #5eead4;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "PingFang SC", "Noto Sans SC", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 15% 10%, #1c3a52 0%, transparent 35%),
        radial-gradient(circle at 80% 90%, #3c2e1f 0%, transparent 30%),
        linear-gradient(160deg, var(--bg) 0%, #09111a 100%);
      min-height: 100vh;
    }
    .frame {
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px 18px 32px;
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 18px;
    }
    .panel {
      background: linear-gradient(180deg, var(--card) 0%, var(--bg-soft) 100%);
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
    }
    .panel-header {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      background: rgba(255, 255, 255, 0.02);
    }
    .title {
      font-size: 16px;
      font-weight: 700;
      letter-spacing: 0.3px;
    }
    .badge {
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      border: 1px solid var(--line);
      color: var(--muted);
      font-family: "JetBrains Mono", monospace;
    }
    .filters {
      display: flex;
      gap: 8px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.12);
    }
    .filters button {
      border: 1px solid var(--line);
      color: var(--muted);
      background: transparent;
      border-radius: 999px;
      padding: 6px 12px;
      cursor: pointer;
      font-family: "JetBrains Mono", monospace;
      font-size: 12px;
    }
    .filters button.active {
      color: #021317;
      border-color: var(--accent);
      background: var(--accent);
      font-weight: 700;
    }
    .list {
      max-height: calc(100vh - 200px);
      overflow-y: auto;
      padding: 12px;
      display: grid;
      gap: 12px;
    }
    .thread {
      border: 1px solid var(--line);
      background: linear-gradient(180deg, var(--card-2) 0%, rgba(34, 52, 70, 0.4) 100%);
      border-radius: 12px;
      padding: 12px 14px;
      animation: rise 220ms ease;
    }
    .thread h3 {
      margin: 0 0 8px;
      font-size: 15px;
      line-height: 1.4;
    }
    .meta {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
      font-family: "JetBrains Mono", monospace;
    }
    .status-open { color: var(--warn); }
    .status-replied { color: var(--ok); }
    .thread p {
      margin: 0;
      line-height: 1.7;
      white-space: pre-wrap;
      font-size: 14px;
    }
    .reply-box {
      margin-top: 10px;
      border-top: 1px dashed var(--line);
      padding-top: 10px;
    }
    .reply {
      background: rgba(0,0,0,0.2);
      border: 1px solid rgba(94, 234, 212, 0.25);
      border-radius: 10px;
      padding: 10px;
      margin-bottom: 8px;
    }
    .timeline {
      max-height: calc(100vh - 120px);
      overflow-y: auto;
      padding: 12px;
      display: grid;
      gap: 8px;
    }
    .event {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 9px 10px;
      background: rgba(0,0,0,0.2);
      font-size: 13px;
      line-height: 1.5;
    }
    .event .time {
      color: var(--muted);
      font-family: "JetBrains Mono", monospace;
      font-size: 11px;
    }
    @keyframes rise {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (max-width: 980px) {
      .frame { grid-template-columns: 1fr; }
      .list, .timeline { max-height: none; }
    }
  </style>
</head>
<body>
  <div class="frame">
    <section class="panel">
      <div class="panel-header">
        <div class="title">AI 协作现场</div>
        <div id="conn" class="badge">connecting</div>
      </div>
      <div class="filters">
        <button class="active" data-status="all">all</button>
        <button data-status="open">open</button>
        <button data-status="replied">replied</button>
      </div>
      <div id="threads" class="list"></div>
    </section>

    <aside class="panel">
      <div class="panel-header">
        <div class="title">现场事件流</div>
        <div class="badge">read-only</div>
      </div>
      <div id="events" class="timeline"></div>
    </aside>
  </div>

<script>
(function () {
  const threadsEl = document.getElementById("threads");
  const eventsEl = document.getElementById("events");
  const connEl = document.getElementById("conn");
  const filterBtns = Array.from(document.querySelectorAll(".filters button"));
  let currentStatus = "all";

  function escapeHtml(text) {
    return String(text)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\"", "&quot;")
      .replaceAll("'", "&#039;");
  }

  function pushEvent(text) {
    const item = document.createElement("div");
    item.className = "event";
    item.innerHTML =
      "<div>" + text + "</div>" +
      "<div class='time'>" + new Date().toLocaleTimeString() + "</div>";
    eventsEl.prepend(item);
    while (eventsEl.children.length > 80) {
      eventsEl.removeChild(eventsEl.lastChild);
    }
  }

  function renderReplies(threadId, replies) {
    const box = document.getElementById("reply-box-" + threadId);
    if (!box) return;
    if (!replies.length) {
      box.innerHTML = "<div class='meta'>等待 reviewer_ai 回帖...</div>";
      return;
    }
    box.innerHTML = replies.map(function (r) {
      return (
        "<div class='reply'>" +
          "<div class='meta'>" + escapeHtml(r.author) + " · " + escapeHtml(r.created_at) + "</div>" +
          "<div>" + escapeHtml(r.body) + "</div>" +
        "</div>"
      );
    }).join("");
  }

  function renderThreads(threads) {
    threadsEl.innerHTML = "";
    if (!threads.length) {
      threadsEl.innerHTML = "<div class='event'>暂无帖子</div>";
      return;
    }

    threads.forEach(function (thread) {
      const card = document.createElement("article");
      card.className = "thread";
      const statusClass = thread.status === "replied" ? "status-replied" : "status-open";
      card.innerHTML =
        "<h3>" + escapeHtml(thread.title) + "</h3>" +
        "<div class='meta'>#" + thread.id + " · " + escapeHtml(thread.author) + " · " +
        "<span class='" + statusClass + "'>" + thread.status + "</span> · " +
        escapeHtml(thread.updated_at) + "</div>" +
        "<p>" + escapeHtml(thread.body) + "</p>" +
        "<div class='reply-box' id='reply-box-" + thread.id + "'></div>";
      threadsEl.appendChild(card);

      fetch("/api/threads/" + thread.id)
        .then(function (r) { return r.json(); })
        .then(function (payload) {
          const replies = (payload.thread && payload.thread.replies) || [];
          renderReplies(thread.id, replies);
        })
        .catch(function () {});
    });
  }

  function loadThreads() {
    fetch("/api/threads?status=" + encodeURIComponent(currentStatus) + "&limit=50")
      .then(function (resp) { return resp.json(); })
      .then(function (payload) { renderThreads(payload.threads || []); })
      .catch(function (err) { pushEvent("拉取帖子失败: " + err); });
  }

  function connectEvents() {
    const ev = new EventSource("/api/events");

    ev.addEventListener("connected", function () {
      connEl.textContent = "live";
      connEl.style.color = "#4ade80";
      pushEvent("事件流已连接");
    });

    ev.addEventListener("thread_created", function (e) {
      const data = JSON.parse(e.data);
      const t = data.content.thread;
      pushEvent("developer_ai 发帖: #" + t.id + " " + escapeHtml(t.title));
      loadThreads();
    });

    ev.addEventListener("thread_replied", function (e) {
      const data = JSON.parse(e.data);
      const t = data.content.thread;
      pushEvent("reviewer_ai 回帖: #" + t.id + " " + escapeHtml(t.title));
      loadThreads();
    });

    ev.addEventListener("heartbeat", function () {
      connEl.textContent = "live";
    });

    ev.onerror = function () {
      connEl.textContent = "reconnecting";
      connEl.style.color = "#fbbf24";
      ev.close();
      setTimeout(connectEvents, 1500);
    };
  }

  filterBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      currentStatus = btn.dataset.status;
      filterBtns.forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      loadThreads();
    });
  });

  loadThreads();
  connectEvents();
  setInterval(loadThreads, 12000);
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
