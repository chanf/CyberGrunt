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
                try: cls._clients[sid].remove(q)
                except ValueError: pass

    @classmethod
    def publish(cls, sid, event_type, content, extra=None):
        packet = {"type": event_type, "content": content, "ts": time.time()}
        if extra: packet.update(extra)
        raw_data = f"data: {json.dumps(packet, ensure_ascii=False)}\n\n"
        
        with cls._lock:
            if sid in cls._clients:
                for q in cls._clients[sid]:
                    q.put(raw_data)

# ============================================================
#  3. Async Task Processor
# ============================================================

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
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_UI.encode("utf-8"))

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length).decode('utf-8'))
        except: self.send_response(400); self.end_headers(); return

        if self.path == "/chat":
            sid = data.get("sid", "default")
            text = data.get("text", "").strip()
            # 1. Immediately acknowledge the request
            self.send_response(202) # Accepted
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "accepted"}).encode("utf-8"))
            
            # 2. Fire up the background engine
            threading.Thread(target=run_agent_task, args=(sid, text), daemon=True).start()
        else:
            self.send_response(404); self.end_headers()

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
    <div id="sidebar">
        <div id="log-header">LIVE AGENT LOGS</div>
        <div id="log-view"></div>
    </div>
    <div id="main">
        <div id="chat"></div>
        <div id="system-status"></div>
        <div id="input-area">
            <input type="text" id="userInput" placeholder="Send a command..." autocomplete="off">
            <button id="sendBtn">Send</button>
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
            const el = document.createElement('div');
            el.className = 'log-item';
            el.textContent = `[${new Date().toLocaleTimeString()}] ${txt}`;
            ui.logs.appendChild(el);
            ui.logs.scrollTop = ui.logs.scrollHeight;
        }

        function addBubble(role, content) {
            const el = document.createElement('div');
            el.className = `bubble ${role}`;
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
