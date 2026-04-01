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

_RECENT_ERROR_LOCK = threading.Lock()
_RECENT_ERROR = None


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
        "loaded_limbs": _loaded_limb_names(),
        "recent_error": _get_recent_error(),
        "ts": datetime.now(CST).isoformat(timespec="seconds"),
    }


def run_agent_task(sid, text):
    """The background worker that executes the agent loop."""
    def on_log(msg):
        EventBus.publish(sid, "log", str(msg))

    try:
        log.info(f"[Task] Starting async task for {sid}: {text[:50]}")
        reply = llm.chat(text, f"web_{sid}", on_log=on_log)
        EventBus.publish(sid, "reply", reply)
    except Exception as e:
        log.error(f"[Task] Error for {sid}: {e}", exc_info=True)
        _record_recent_error("run_agent_task", e)
        EventBus.publish(sid, "error", str(e))
    finally:
        EventBus.publish(sid, "done", "Task finished")

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
            sid = data.get("sid", "default")
            text = data.get("text", "").strip()
            # 1. Immediately acknowledge the request
            self._send_json(202, {"status": "accepted"})
            
            # 2. Fire up the background engine
            threading.Thread(target=run_agent_task, args=(sid, text), daemon=True).start()
        else:
            self._send_json(404, {"error": "not found"})

    def handle_test_health(self):
        self._send_json(200, _build_test_health_payload())

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
        </div>
    </div>

    <script>
        const ui = {
            chat: document.getElementById('chat'),
            logs: document.getElementById('log-view'),
            input: document.getElementById('userInput'),
            btn: document.getElementById('sendBtn'),
            status: document.getElementById('system-status')
        };
        const SID = Math.random().toString(36).substring(7);
        let isComposing = false; // Track Chinese IME state

        ui.input.addEventListener('compositionstart', () => { isComposing = true; });
        ui.input.addEventListener('compositionend', () => { isComposing = false; });

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
                    case 'reply':
                        addBubble('bot', data.content);
                        break;
                    case 'error':
                        addBubble('bot', '<strong>Error:</strong> ' + data.content);
                        break;
                    case 'done':
                        ui.status.style.display = 'none';
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
            if (!text || ui.btn.disabled) return;

            addBubble('user', text);
            ui.input.value = '';
            ui.btn.disabled = true;

            try {
                const resp = await fetch('/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text: text, sid: SID})
                });
                if (!resp.ok) throw new Error("Connection failed");
                // Reset button immediately - we are async now!
                ui.btn.disabled = false;
                ui.input.focus();
            } catch (err) {
                addBubble('bot', '<strong>System:</strong> Failed to send command.');
                ui.btn.disabled = false;
            }
        }

        ui.btn.onclick = sendCommand;
        ui.input.onkeydown = (e) => { 
            if(e.key === 'Enter' && !isComposing) sendCommand(); 
        };
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
