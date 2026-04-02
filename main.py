"""
CyberGrunt - Asynchronous Decoupled Gateway
- EventBus: Persistent SSE stream for real-time updates.
- Async Chat: Non-blocking command execution.
- Robust State Machine: Fully decoupled Frontend and Backend.
"""

import os
import json
import time
import logging
import uuid
import traceback
import threading
import queue
import urllib.parse
import ctypes
from typing import Any, Dict, Optional, Tuple
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime, timezone, timedelta

# ============================================================
#  1. Global Configuration & State
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("agent")
CST = timezone(timedelta(hours=8))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("AGENT_CONFIG", os.path.join(BASE_DIR, "config.json"))

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONF = json.load(f)

PORT = CONF.get("port", 8088) # Using 8088 to avoid common macOS conflicts
WORKSPACE = os.path.abspath(CONF.get("workspace", "./workspace"))
OWNER_IDS = set(str(x) for x in CONF.get("owner_ids", []))

# Paths initialization
DIRS = {
    "sessions": os.path.join(BASE_DIR, "sessions"),
    "files": os.path.join(WORKSPACE, "files"),
    "memory": os.path.join(BASE_DIR, "memory_db")
}
for d in DIRS.values(): os.makedirs(d, exist_ok=True)

# Modules Initialization
import messaging
import scheduler
from brain import central as llm
from brain import memory as mem_mod
from limbs import hub as limbs_hub
messaging.init(CONF["messaging"])
llm.init(CONF["models"], WORKSPACE, next(iter(OWNER_IDS), "admin"), DIRS["sessions"])
scheduler.init(os.path.join(BASE_DIR, "jobs.json"), llm.chat)
limbs_hub.init_extra(CONF)
mem_mod.init(CONF, CONF.get('models', {}), DIRS["memory"])

SELF_REPAIR_JOB_NAME = "daily_self_repair_0200"
SELF_REPAIR_CRON_EXPR = "0 2 * * *"
SELF_REPAIR_MESSAGE = (
    "【系统计划任务】执行每日自修复闭环。\n"
    "步骤要求：\n"
    "1) 调用 self_repair_loop 工具（建议参数 {\"disk_free_mb_threshold\": 1024, \"cleanup_limit_mb\": 256}）。\n"
    "2) 将 self_repair_loop 的完整输出通过 message 工具发送给主人。\n"
    "3) 返回一句执行结果摘要。"
)


def _ensure_daily_self_repair_schedule():
    """Ensure fixed daily self-repair schedule at 02:00."""
    try:
        result = scheduler.add(
            {
                "name": SELF_REPAIR_JOB_NAME,
                "message": SELF_REPAIR_MESSAGE,
                "cron_expr": SELF_REPAIR_CRON_EXPR,
                "once": False,
            }
        )
        log.info("[boot] ensured daily self-repair schedule: %s", result)
    except Exception as exc:
        log.error("[boot] failed to ensure daily self-repair schedule: %s", exc, exc_info=True)


_ensure_daily_self_repair_schedule()


FORUM_MONITOR_JOB_NAME = "forum_health_monitor"
FORUM_MONITOR_CRON_EXPR = "*/2 * * * *"  # 每2分钟

def _ensure_forum_monitor_schedule():
    """Ensure forum health monitor runs every 2 minutes."""
    try:
        result = scheduler.add(
            {
                "name": FORUM_MONITOR_JOB_NAME,
                "message": "检查论坛健康状态并自动修复",
                "cron_expr": FORUM_MONITOR_CRON_EXPR,
                "once": False,
            }
        )
        log.info("[boot] ensured forum monitor schedule: %s", result)
    except Exception as exc:
        log.error("[boot] failed to ensure forum monitor schedule: %s", exc, exc_info=True)

_ensure_forum_monitor_schedule()

_RECENT_ERROR_LOCK = threading.Lock()
_RECENT_ERROR = None
_ACTIVE_TASKS_LOCK = threading.Lock()
_ACTIVE_TASKS: Dict[str, Dict[str, Any]] = {}


class TaskAbortRequested(Exception):
    """Raised asynchronously to abort a running agent task."""


def _record_recent_error(where, exc):
    """Store latest stack trace for /api/test/health diagnostics."""
    global _RECENT_ERROR
    with _RECENT_ERROR_LOCK:
        _RECENT_ERROR = {
            "where": where,
            "message": str(exc),
            "stack": traceback.format_exc(limit=20),
            "ts": datetime.now(CST).isoformat(timespec="seconds"),
        }


def _get_recent_error():
    with _RECENT_ERROR_LOCK:
        return dict(_RECENT_ERROR) if _RECENT_ERROR else None


def _active_task_count() -> int:
    with _ACTIVE_TASKS_LOCK:
        return sum(
            1
            for task in _ACTIVE_TASKS.values()
            if task.get("thread") and task["thread"].is_alive()
        )


def _register_active_task(sid: str, owner_id: str, worker: threading.Thread) -> Tuple[bool, str]:
    with _ACTIVE_TASKS_LOCK:
        existing = _ACTIVE_TASKS.get(sid)
        if existing and existing.get("thread") and existing["thread"].is_alive():
            return False, "task already running for this sid"
        _ACTIVE_TASKS[sid] = {
            "thread": worker,
            "owner_id": owner_id,
            "started_at": time.time(),
            "stop_requested": False,
        }
        return True, ""


def _clear_active_task(sid: str, worker: Optional[threading.Thread] = None) -> None:
    with _ACTIVE_TASKS_LOCK:
        existing = _ACTIVE_TASKS.get(sid)
        if not existing:
            return
        if worker is not None and existing.get("thread") is not worker:
            return
        _ACTIVE_TASKS.pop(sid, None)


def _raise_async_exception(thread_id: int, exc_type: type[BaseException]) -> bool:
    result = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(thread_id),
        ctypes.py_object(exc_type),
    )
    if result == 0:
        return False
    if result > 1:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), 0)
        return False
    return True


def _request_task_stop(sid: str, requester_id: str) -> Dict[str, Any]:
    with _ACTIVE_TASKS_LOCK:
        task = _ACTIVE_TASKS.get(sid)
        if not task:
            return {"ok": False, "status_code": 404, "error": "no active task for sid"}

        owner_id = str(task.get("owner_id", sid))
        if requester_id != owner_id and requester_id not in OWNER_IDS:
            return {"ok": False, "status_code": 403, "error": "not allowed to stop this task"}

        worker = task.get("thread")
        if not worker or not worker.is_alive() or worker.ident is None:
            _ACTIVE_TASKS.pop(sid, None)
            return {"ok": False, "status_code": 409, "error": "task is not running"}

        task["stop_requested"] = True
        target_thread_id = int(worker.ident)

    interrupted = _raise_async_exception(target_thread_id, TaskAbortRequested)
    if not interrupted:
        return {"ok": False, "status_code": 409, "error": "failed to interrupt running task"}
    return {"ok": True, "status_code": 200, "sid": sid}


def _extract_tool_name(log_line: str) -> Optional[str]:
    prefix = "Action: Calling tool '"
    if not log_line.startswith(prefix):
        return None
    tail = log_line[len(prefix):]
    end_idx = tail.find("'")
    if end_idx <= 0:
        return None
    return tail[:end_idx]


def _structured_event_from_log(
    log_line: str,
    last_tool: Optional[str],
) -> Optional[Tuple[str, str, Dict[str, Any], Optional[str]]]:
    raw = str(log_line or "").strip()
    if not raw:
        return None
    if raw.startswith("Thought:"):
        thought = raw[len("Thought:"):].strip() or raw
        return "thought", thought, {}, last_tool

    tool_name = _extract_tool_name(raw)
    if tool_name:
        return "tool_start", raw, {"tool": tool_name}, tool_name

    if raw.startswith("Result:"):
        extra: Dict[str, Any] = {"status": "ok"}
        if last_tool:
            extra["tool"] = last_tool
        return "tool_end", raw, extra, None

    if raw.startswith("Error:"):
        extra = {"status": "error"}
        if last_tool:
            extra["tool"] = last_tool
        return "tool_end", raw, extra, None

    return None

# ============================================================
#  2. Event Bus (The Message Backbone)
# ============================================================

class EventBus:
    """Manages active SSE connections and broadcasts events per session ID (sid)."""
    _clients = {} # sid -> [queue.Queue]
    _lock = threading.Lock()

    @classmethod
    def subscribe(cls, sid):
        q = queue.Queue()
        with cls._lock:
            cls._clients.setdefault(sid, []).append(q)
        return q

    @classmethod
    def unsubscribe(cls, sid, q):
        with cls._lock:
            if sid in cls._clients:
                try:
                    cls._clients[sid].remove(q)
                except ValueError:
                    pass
                if not cls._clients[sid]:
                    del cls._clients[sid]

    @classmethod
    def publish(cls, sid, event_type, content, extra=None):
        packet = {"type": event_type, "content": content, "ts": time.time()}
        if extra: packet.update(extra)
        raw_data = f"data: {json.dumps(packet, ensure_ascii=False)}\n\n"
        
        with cls._lock:
            if sid in cls._clients:
                for q in cls._clients[sid]:
                    q.put(raw_data)

    @classmethod
    def stats(cls):
        with cls._lock:
            active_sessions = sum(1 for _, queues in cls._clients.items() if queues)
            active_connections = sum(len(queues) for queues in cls._clients.values())
        return {
            "active_sessions": active_sessions,
            "active_connections": active_connections,
        }

# ============================================================
#  3. Async Task Processor
# ============================================================


def _loaded_limb_names():
    try:
        return sorted(name for name, _ in limbs_hub.Registry.items())
    except Exception:
        return []


def _build_test_health_payload():
    stats = EventBus.stats()
    return {
        "ok": True,
        "active_sessions": stats["active_sessions"],
        "active_connections": stats["active_connections"],
        "active_tasks": _active_task_count(),
        "loaded_limbs": _loaded_limb_names(),
        "recent_error": _get_recent_error(),
        "ts": datetime.now(CST).isoformat(timespec="seconds"),
    }


def run_agent_task(sid, text):
    """The background worker that executes the agent loop."""
    worker = threading.current_thread()
    current_tool = None
    done_message = "Task finished"

    def on_log(msg):
        nonlocal current_tool
        raw = str(msg)
        EventBus.publish(sid, "log", raw)

        structured = _structured_event_from_log(raw, current_tool)
        if structured:
            event_type, content, extra, next_tool = structured
            current_tool = next_tool
            EventBus.publish(sid, event_type, content, extra)

    try:
        log.info(f"[Task] Starting async task for {sid}: {text[:50]}")
        reply = llm.chat(text, f"web_{sid}", on_log=on_log)
        EventBus.publish(sid, "reply", reply)
    except TaskAbortRequested:
        done_message = "Task aborted"
        log.info(f"[Task] Aborted by request for {sid}")
        EventBus.publish(sid, "lifecycle", "Task aborted", {"phase": "aborted"})
    except Exception as e:
        done_message = "Task failed"
        log.error(f"[Task] Error for {sid}: {e}", exc_info=True)
        _record_recent_error("run_agent_task", e)
        EventBus.publish(sid, "error", str(e))
    finally:
        _clear_active_task(sid, worker)
        EventBus.publish(sid, "done", done_message)

# ============================================================
#  4. HTTP Server Logic
# ============================================================

class AgentRouter(BaseHTTPRequestHandler):
    def handle(self):
        """Override handle to suppress common socket errors on disconnect."""
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            if "Errno 54" in str(e) or "Errno 32" in str(e):
                pass
            else:
                raise

    def log_message(self, format, *args): pass 

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/events":
            self.handle_events(parsed)
        elif parsed.path == "/api/test/health":
            self.handle_test_health()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_UI.encode("utf-8"))

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception as e:
            _record_recent_error("http_post_parse", e)
            self._send_json(400, {"error": "invalid json body"})
            return

        if self.path == "/chat":
            sid = str(data.get("sid", "default"))
            text = str(data.get("text", "")).strip()
            if not text:
                self._send_json(400, {"error": "empty text"})
                return
            owner_id = str(data.get("owner_id", sid))

            worker = threading.Thread(target=run_agent_task, args=(sid, text), daemon=True)
            ok, reason = _register_active_task(sid, owner_id, worker)
            if not ok:
                self._send_json(409, {"error": reason, "sid": sid})
                return

            self._send_json(202, {"status": "accepted", "sid": sid})
            worker.start()
        elif self.path == "/api/task/stop":
            self._handle_task_stop(data)
        else:
            self._send_json(404, {"error": "not found"})

    def handle_test_health(self):
        self._send_json(200, _build_test_health_payload())

    def _handle_task_stop(self, data):
        sid = str(data.get("sid", "")).strip()
        if not sid:
            self._send_json(400, {"error": "missing sid"})
            return
        requester_id = str(data.get("requester_id", sid)).strip() or sid
        result = _request_task_stop(sid, requester_id)
        if result.get("ok"):
            self._send_json(200, {"ok": True, "sid": sid, "phase": "aborting"})
            return
        self._send_json(int(result.get("status_code", 500)), result)

    def handle_events(self, parsed):
        """Standard SSE endpoint using the EventBus subscription."""
        params = urllib.parse.parse_qs(parsed.query)
        sid = params.get("sid", ["default"])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = EventBus.subscribe(sid)
        log.info(f"[Events] Client connected: {sid}")
        try:
            while True:
                try:
                    data = q.get(timeout=15) # Heartbeat/Keepalive
                    self.wfile.write(data.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Keep-alive ping
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except:
            log.info(f"[Events] Client disconnected: {sid}")
        finally:
            EventBus.unsubscribe(sid, q)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def main():
    scheduler.start()
    log.info(f"--- 7/24 Office (Async Mode) ready at http://localhost:{PORT} ---")
    server = ThreadedServer(("0.0.0.0", PORT), AgentRouter)
    server.serve_forever()

# ============================================================
#  5. Frontend UI (Raw String)
# ============================================================

HTML_UI = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>7/24 Office - Async Console</title>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root { --accent: #007aff; --sidebar: #1c1c1e; }
        body { font-family: -apple-system, sans-serif; display: flex; height: 100vh; margin: 0; background: #f5f5f7; overflow: hidden; }
        
        #sidebar { width: 340px; background: var(--sidebar); color: #8e8e93; display: flex; flex-direction: column; }
        #log-header { padding: 15px; color: white; font-weight: bold; border-bottom: 1px solid #333; font-size: 13px; }
        #log-view { flex: 1; overflow-y: auto; padding: 15px; font-family: 'SF Mono', monospace; font-size: 11px; line-height: 1.4; }
        .log-item { margin-bottom: 6px; border-left: 2px solid #444; padding-left: 10px; word-break: break-all; }
        .log-event-thought { border-left-color: #8b5cf6; }
        .log-event-tool-call { border-left-color: #3b82f6; }
        .log-event-tool-success { border-left-color: #22c55e; }
        .log-event-llm-timeout { border-left-color: #f59e0b; }
        .log-event-tool-quality { border-left-color: #14b8a6; }
        .log-event-error { border-left-color: #ef4444; }
        
        #main { flex: 1; display: flex; flex-direction: column; background: white; position: relative; }
        #chat { flex: 1; overflow-y: auto; padding: 30px; display: flex; flex-direction: column; gap: 20px; }
        .bubble { max-width: 85%; padding: 14px 18px; border-radius: 18px; font-size: 15px; line-height: 1.6; }
        .user { align-self: flex-end; background: var(--accent); color: white; border-bottom-right-radius: 4px; }
        .bot { align-self: flex-start; background: #f2f2f7; color: #1d1d1f; border-bottom-left-radius: 4px; }
        
        /* Markdown rendering */
        .bot p { margin: 8px 0; }
        .bot pre { background: #28282b; color: #eee; padding: 15px; border-radius: 10px; overflow-x: auto; margin: 10px 0; }
        .bot code { font-family: monospace; background: rgba(0,0,0,0.05); padding: 2px 4px; border-radius: 4px; }
        .bot table { border-collapse: collapse; width: 100%; margin: 10px 0; }
        .bot th, .bot td { border: 1px solid #d1d1d6; padding: 8px; }

        #input-area { padding: 20px 30px; border-top: 1px solid #eee; display: flex; gap: 15px; background: white; z-index: 10; }
        input { flex: 1; padding: 14px 20px; border: 1px solid #ddd; border-radius: 25px; outline: none; font-size: 16px; }
        button { background: var(--accent); color: white; border: none; padding: 0 25px; border-radius: 25px; cursor: pointer; font-weight: bold; }
        #stopBtn { background: #dc2626; display: none; }
        button:disabled { opacity: 0.5; }

        #system-status { position: absolute; bottom: 85px; left: 50%; transform: translateX(-50%); 
                         background: rgba(255,255,255,0.9); padding: 5px 15px; border-radius: 15px; 
                         font-size: 12px; color: #888; border: 1px solid #eee; display: none; }
    </style>
</head>
<body>
    <div id="sidebar" data-testid="log-panel">
        <div id="log-header">LIVE AGENT LOGS</div>
        <div id="log-view" data-testid="log-stream"></div>
    </div>
    <div id="main">
        <div id="chat" data-testid="chat-stream"></div>
        <div id="system-status" data-testid="system-status-bar"></div>
        <div id="input-area" data-testid="input-area">
            <input type="text" id="userInput" data-testid="chat-input" placeholder="Send a command..." autocomplete="off">
            <button id="sendBtn" data-testid="send-button">Send</button>
            <button id="stopBtn" data-testid="stop-button">Stop</button>
        </div>
    </div>

    <script>
        const ui = {
            chat: document.getElementById('chat'),
            logs: document.getElementById('log-view'),
            input: document.getElementById('userInput'),
            btn: document.getElementById('sendBtn'),
            stopBtn: document.getElementById('stopBtn'),
            status: document.getElementById('system-status')
        };
        const SID = Math.random().toString(36).substring(7);
        let isComposing = false; // Track Chinese IME state
        let taskRunning = false;

        ui.input.addEventListener('compositionstart', () => { isComposing = true; });
        ui.input.addEventListener('compositionend', () => { isComposing = false; });

        function setTaskRunning(running) {
            taskRunning = !!running;
            ui.btn.disabled = taskRunning;
            ui.stopBtn.style.display = taskRunning ? 'inline-block' : 'none';
            if (!taskRunning) {
                ui.status.style.display = 'none';
            }
        }

        // --- Persistent Event Connection ---
        function connectEvents() {
            const ev = new EventSource(`/events?sid=${SID}`);
            ev.onmessage = (e) => {
                const data = JSON.parse(e.data);
                switch(data.type) {
                    case 'log':
                        appendLog(data.content);
                        ui.status.style.display = 'block';
                        ui.status.textContent = 'Agent: ' + data.content.substring(0, 40) + '...';
                        break;
                    case 'thought':
                        ui.status.style.display = 'block';
                        ui.status.textContent = 'Thought: ' + data.content;
                        break;
                    case 'tool_start':
                        ui.status.style.display = 'block';
                        ui.status.textContent = 'Tool running: ' + (data.tool || 'unknown');
                        break;
                    case 'tool_end':
                        ui.status.style.display = 'block';
                        ui.status.textContent = 'Tool finished: ' + (data.tool || 'unknown');
                        break;
                    case 'reply':
                        addBubble('bot', data.content);
                        break;
                    case 'error':
                        addBubble('bot', '<strong>Error:</strong> ' + data.content);
                        setTaskRunning(false);
                        break;
                    case 'lifecycle':
                        if (data.phase === 'aborted') {
                            addBubble('bot', '<strong>System:</strong> Task aborted.');
                            setTaskRunning(false);
                        }
                        break;
                    case 'done':
                        setTaskRunning(false);
                        break;
                }
            };
            ev.onerror = () => {
                ev.close();
                setTimeout(connectEvents, 2000); // Auto-reconnect
            };
        }
        connectEvents();

        function appendLog(txt) {
            const marker = classifyLogEvent(txt);
            const el = document.createElement('div');
            el.className = `log-item ${marker.className}`;
            el.setAttribute('data-testid', marker.testId);
            el.textContent = `[${new Date().toLocaleTimeString()}] ${txt}`;
            ui.logs.appendChild(el);
            ui.logs.scrollTop = ui.logs.scrollHeight;
        }

        function classifyLogEvent(txt) {
            const raw = String(txt || '');
            if (raw.startsWith('Thought:')) {
                return {className: 'log-event-thought', testId: 'log-event-thought'};
            }
            if (raw.includes('[tool_quality]')) {
                return {className: 'log-event-tool-quality', testId: 'log-event-tool-quality'};
            }
            if (raw.startsWith('Action:')) {
                return {className: 'log-event-tool-call', testId: 'log-event-tool-call'};
            }
            if (raw.startsWith('Result:')) {
                return {className: 'log-event-tool-success', testId: 'log-event-tool-success'};
            }
            if (/timeout|timed out/i.test(raw)) {
                return {className: 'log-event-llm-timeout', testId: 'log-event-llm-timeout'};
            }
            if (raw.startsWith('Error:') || raw.toLowerCase().includes('error')) {
                return {className: 'log-event-error', testId: 'log-event-error'};
            }
            return {className: 'log-event-generic', testId: 'log-event-generic'};
        }

        function addBubble(role, content) {
            const el = document.createElement('div');
            el.className = `bubble ${role}`;
            el.setAttribute('data-testid', role === 'user' ? 'chat-bubble-user' : 'chat-bubble-bot');
            if (role === 'bot') el.innerHTML = marked.parse(content);
            else el.textContent = content;
            ui.chat.appendChild(el);
            ui.chat.scrollTop = ui.chat.scrollHeight;
        }

        async function sendCommand() {
            const text = ui.input.value.trim();
            if (!text || taskRunning) return;

            addBubble('user', text);
            ui.input.value = '';
            setTaskRunning(true);

            try {
                const resp = await fetch('/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text: text, sid: SID, owner_id: SID})
                });
                if (!resp.ok) throw new Error("Connection failed");
                ui.input.focus();
            } catch (err) {
                addBubble('bot', '<strong>System:</strong> Failed to send command.');
                setTaskRunning(false);
            }
        }

        async function stopTask() {
            if (!taskRunning) return;
            try {
                const resp = await fetch('/api/task/stop', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({sid: SID, requester_id: SID})
                });
                const payload = await resp.json();
                if (!resp.ok || !payload.ok) {
                    const msg = payload.error || 'stop failed';
                    addBubble('bot', '<strong>System:</strong> Stop failed: ' + msg);
                    return;
                }
                ui.status.style.display = 'block';
                ui.status.textContent = 'Stopping task...';
            } catch (err) {
                addBubble('bot', '<strong>System:</strong> Stop request failed.');
            }
        }

        ui.btn.onclick = sendCommand;
        ui.stopBtn.onclick = stopTask;
        ui.input.onkeydown = (e) => { 
            if(e.key === 'Enter' && !isComposing) sendCommand(); 
        };
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
