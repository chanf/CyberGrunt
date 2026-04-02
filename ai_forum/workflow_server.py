"""
Workflow Server - HTTP API and Web UI for workflow management.
Port: 5678 (as requested by user)
"""

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
from typing import Any, Dict, Optional

if __package__ is None or __package__ == "":
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_forum.workflow_store import WorkflowStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("workflow_server")


class EventBus:
    """SSE event bus for real-time updates."""

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
        packet = {"type": event_type, "content": content, "ts": time.time()}
        with self._lock:
            targets = list(self._subs)
        for q in targets:
            q.put(packet)


class WorkflowApp:
    def __init__(self, store: WorkflowStore, bus: Optional[EventBus] = None):
        self.store = store
        self.bus = bus or EventBus()


APP: Optional[WorkflowApp] = None


def _resolve_db_path() -> str:
    """Resolve workflow database path."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "workflows.db")


def load_app() -> WorkflowApp:
    """Load or create the workflow app."""
    global APP
    if APP is None:
        db_path = _resolve_db_path()
        store = WorkflowStore(db_path)
        APP = WorkflowApp(store)
        log.info(f"[workflow] Workflow system initialized with DB: {db_path}")
    return APP


class WorkflowHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

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

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        app = load_app()
        parsed = urllib.parse.urlparse(self.path)

        # Web UI
        if parsed.path == "/" or parsed.path == "/workflows":
            self._send_html(WORKFLOW_UI)
            return

        # SSE events
        if parsed.path == "/events":
            self._handle_events()
            return

        # Health check
        if parsed.path == "/healthz":
            self._send_json(200, {"ok": True})
            return

        # API: List workflows
        if parsed.path == "/api/workflows":
            self._handle_list_workflows(parsed)
            return

        # API: Get workflow by ID
        if parsed.path.startswith("/api/workflows/") and parsed.path.count("/") == 3:
            try:
                workflow_id = int(parsed.path.split("/")[-1])
                self._handle_get_workflow(workflow_id)
            except ValueError:
                self._send_json(400, {"error": "Invalid workflow ID"})
            return

        # API: List workflow comments
        if parsed.path.startswith("/api/workflows/") and "/comments" in parsed.path:
            try:
                workflow_id = int(parsed.path.split("/")[3])
                self._handle_list_comments(workflow_id)
            except (ValueError, IndexError):
                self._send_json(400, {"error": "Invalid workflow ID"})
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        app = load_app()
        parsed = urllib.parse.urlparse(self.path)

        # API: Create workflow
        if parsed.path == "/api/workflows":
            self._handle_create_workflow()
            return

        # API: Claim workflow
        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/claim"):
            try:
                workflow_id = int(parsed.path.split("/")[3])
                self._handle_claim_workflow(workflow_id)
            except (ValueError, IndexError):
                self._send_json(400, {"error": "Invalid workflow ID"})
            return

        # API: Unclaim workflow
        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/unclaim"):
            try:
                workflow_id = int(parsed.path.split("/")[3])
                self._handle_unclaim_workflow(workflow_id)
            except (ValueError, IndexError):
                self._send_json(400, {"error": "Invalid workflow ID"})
            return

        # API: Reassign workflow
        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/reassign"):
            try:
                workflow_id = int(parsed.path.split("/")[3])
                self._handle_reassign_workflow(workflow_id)
            except (ValueError, IndexError):
                self._send_json(400, {"error": "Invalid workflow ID"})
            return

        # API: Update workflow status
        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/status"):
            try:
                workflow_id = int(parsed.path.split("/")[3])
                self._handle_set_status(workflow_id)
            except (ValueError, IndexError):
                self._send_json(400, {"error": "Invalid workflow ID"})
            return

        # API: Add comment
        if parsed.path.startswith("/api/workflows/") and "/comments" in parsed.path and parsed.path.count("/") == 4:
            try:
                workflow_id = int(parsed.path.split("/")[3])
                self._handle_add_comment(workflow_id)
            except (ValueError, IndexError):
                self._send_json(400, {"error": "Invalid workflow ID"})
            return

        self._send_json(404, {"error": "Not found"})

    # ==================== Handlers ====================

    def _handle_list_workflows(self, parsed):
        params = urllib.parse.parse_qs(parsed.query)
        status = params.get("status", [None])[0]
        assignee = params.get("assignee", [None])[0]
        workflow_type = params.get("type", [None])[0]
        priority = params.get("priority", [None])[0]
        created_by = params.get("created_by", [None])[0]
        limit = int(params.get("limit", [50])[0])

        try:
            workflows = load_app().store.list_workflows(status, assignee, workflow_type, priority, created_by, limit)
            self._send_json(200, {"workflows": workflows})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})

    def _handle_get_workflow(self, workflow_id: int):
        try:
            workflow = load_app().store.get_workflow_by_id(workflow_id)
            comments = load_app().store.list_workflow_comments(workflow_id)
            self._send_json(200, {"workflow": workflow, "comments": comments})
        except ValueError as e:
            self._send_json(404, {"error": str(e)})

    def _handle_create_workflow(self):
        try:
            data = self._read_json()
            title = data.get("title")
            description = data.get("description")
            workflow_type = data.get("type")
            priority = data.get("priority")
            created_by = data.get("created_by")
            estimate_hours = data.get("estimate_hours")
            related_thread_id = data.get("related_thread_id")

            if not all([title, description, workflow_type, priority, created_by]):
                self._send_json(400, {"error": "Missing required fields"})
                return

            workflow = load_app().store.create_workflow(
                title, description, workflow_type, priority, created_by, estimate_hours, related_thread_id
            )

            # Publish event
            load_app().bus.publish("workflow_created", workflow)

            self._send_json(201, {"workflow": workflow})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:
            log.error(f"[workflow] Failed to create workflow: {e}")
            self._send_json(500, {"error": str(e)})

    def _handle_claim_workflow(self, workflow_id: int):
        try:
            data = self._read_json()
            assignee = data.get("assignee")

            if not assignee:
                self._send_json(400, {"error": "assignee is required"})
                return

            workflow = load_app().store.claim_workflow(workflow_id, assignee)

            # Publish event
            load_app().bus.publish("workflow_claimed", workflow)

            self._send_json(200, {"workflow": workflow})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})

    def _handle_unclaim_workflow(self, workflow_id: int):
        try:
            data = self._read_json()
            assignee = data.get("assignee")
            reason = data.get("reason", "")

            if not assignee:
                self._send_json(400, {"error": "assignee is required"})
                return

            workflow = load_app().store.unclaim_workflow(workflow_id, assignee, reason)

            # Publish event
            load_app().bus.publish("workflow_unclaimed", workflow)

            self._send_json(200, {"workflow": workflow})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})

    def _handle_reassign_workflow(self, workflow_id: int):
        try:
            data = self._read_json()
            from_assignee = data.get("from")
            to_assignee = data.get("to")
            reason = data.get("reason", "")

            if not all([from_assignee, to_assignee]):
                self._send_json(400, {"error": "from and to are required"})
                return

            workflow = load_app().store.reassign_workflow(workflow_id, from_assignee, to_assignee, reason)

            # Publish event
            load_app().bus.publish("workflow_reassigned", workflow)

            self._send_json(200, {"workflow": workflow})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})

    def _handle_set_status(self, workflow_id: int):
        try:
            data = self._read_json()
            status = data.get("status")
            updated_by = data.get("updated_by")
            note = data.get("note", "")

            if not all([status, updated_by]):
                self._send_json(400, {"error": "status and updated_by are required"})
                return

            workflow = load_app().store.set_workflow_status(workflow_id, status, updated_by, note)

            # Publish event
            load_app().bus.publish("workflow_status_changed", workflow)

            self._send_json(200, {"workflow": workflow})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})

    def _handle_add_comment(self, workflow_id: int):
        try:
            data = self._read_json()
            author = data.get("author")
            body = data.get("body")
            comment_type = data.get("comment_type", "comment")

            if not all([author, body]):
                self._send_json(400, {"error": "author and body are required"})
                return

            comment = load_app().store.add_comment(workflow_id, author, body, comment_type)

            # Publish event
            load_app().bus.publish("workflow_comment_added", {"workflow_id": workflow_id, "comment": comment})

            self._send_json(201, {"comment": comment})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})

    def _handle_list_comments(self, workflow_id: int):
        try:
            limit = 100
            comments = load_app().store.list_workflow_comments(workflow_id, limit)
            self._send_json(200, {"comments": comments})
        except ValueError as e:
            self._send_json(404, {"error": str(e)})

    def _handle_events(self):
        app = load_app()
        q = app.bus.subscribe()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        log.info(f"[workflow] SSE client connected")
        try:
            while True:
                try:
                    data = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except:
            pass
        finally:
            app.bus.unsubscribe(q)
            log.info(f"[workflow] SSE client disconnected")


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    load_app()
    PORT = 5678
    server = ThreadedServer(("0.0.0.0", PORT), WorkflowHandler)
    log.info(f"=== Workflow System ready at http://localhost:{PORT} ===")
    server.serve_forever()


# ==================== Web UI ====================

WORKFLOW_UI = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI 工作流管理系统</title>
    <style>
        :root {
            --bg: #f8fafc;
            --card-bg: #ffffff;
            --text: #1e293b;
            --subtext: #64748b;
            --border: #e2e8f0;
            --primary: #3b82f6;
            --success: #22c55e;
            --warning: #f59e0b;
            --danger: #ef4444;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, sans-serif; background: var(--bg); color: var(--text); padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }

        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
        .header h1 { font-size: 24px; font-weight: bold; }
        .header a { color: var(--primary); text-decoration: none; }

        .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }
        .stat-card { background: var(--card-bg); border-radius: 12px; padding: 20px; border: 1px solid var(--border); }
        .stat-card h3 { font-size: 14px; color: var(--subtext); margin-bottom: 8px; }
        .stat-card .value { font-size: 32px; font-weight: bold; }

        .filters { background: var(--card-bg); border-radius: 12px; padding: 16px; margin-bottom: 24px; border: 1px solid var(--border); display: flex; gap: 12px; flex-wrap: wrap; }
        .filters select, .filters input { padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; font-size: 14px; }
        .filters button { padding: 8px 16px; background: var(--primary); color: white; border: none; border-radius: 6px; cursor: pointer; }

        .workflow-list { display: grid; gap: 16px; }
        .workflow-card { background: var(--card-bg); border-radius: 12px; padding: 20px; border: 1px solid var(--border); }
        .workflow-card .header { display: flex; justify-content: space-between; margin-bottom: 12px; }
        .workflow-card .title { font-size: 18px; font-weight: bold; }
        .workflow-card .meta { display: flex; gap: 16px; font-size: 14px; color: var(--subtext); margin-bottom: 12px; }
        .workflow-card .description { font-size: 14px; line-height: 1.6; margin-bottom: 16px; }
        .workflow-card .actions { display: flex; gap: 8px; }
        .workflow-card button { padding: 6px 12px; border: 1px solid var(--border); border-radius: 6px; background: white; cursor: pointer; font-size: 14px; }
        .workflow-card button:hover { background: var(--bg); }

        .badge { padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }
        .badge.p0 { background: #fecaca; color: #dc2626; }
        .badge.p1 { background: #fed7aa; color: #ea580c; }
        .badge.p2 { background: #fef08a; color: #ca8a04; }
        .badge.p3 { background: #dbeafe; color: #2563eb; }
        .badge.open { background: #e2e8f0; color: #475569; }
        .badge.assigned { background: #dbeafe; color: #2563eb; }
        .badge.in_progress { background: #fef3c7; color: #d97706; }
        .badge.completed { background: #dcfce7; color: #16a34a; }
        .badge.blocked { background: #fee2e2; color: #dc2626; }

        .empty { text-align: center; padding: 60px 20px; color: var(--subtext); }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📋 AI 工作流管理系统</h1>
            <a href="/api/workflows" target="_blank">API 文档</a>
        </div>

        <div class="stats">
            <div class="stat-card">
                <h3>可认领</h3>
                <div class="value" id="open-count">-</div>
            </div>
            <div class="stat-card">
                <h3>进行中</h3>
                <div class="value" id="progress-count">-</div>
            </div>
            <div class="stat-card">
                <h3>已完成</h3>
                <div class="value" id="completed-count">-</div>
            </div>
        </div>

        <div class="filters">
            <select id="filter-status">
                <option value="">所有状态</option>
                <option value="open">可认领</option>
                <option value="assigned">已认领</option>
                <option value="in_progress">进行中</option>
                <option value="completed">已完成</option>
                <option value="blocked">已阻塞</option>
            </select>
            <select id="filter-priority">
                <option value="">所有优先级</option>
                <option value="p0">P0 - 紧急</option>
                <option value="p1">P1 - 高</option>
                <option value="p2">P2 - 中</option>
                <option value="p3">P3 - 低</option>
            </select>
            <select id="filter-assignee">
                <option value="">所有人</option>
                <option value="IronGate">IronGate</option>
                <option value="Forge">Forge</option>
                <option value="Shadow">Shadow</option>
            </select>
            <button onclick="refreshWorkflows()">刷新</button>
            <button onclick="showCreateModal()">创建工作流</button>
        </div>

        <div class="workflow-list" id="workflow-list">
            <div class="empty">加载中...</div>
        </div>
    </div>

    <script>
        let workflows = [];

        function fetchWorkflows() {
            const status = document.getElementById('filter-status').value;
            const priority = document.getElementById('filter-priority').value;
            const assignee = document.getElementById('filter-assignee').value;

            let url = '/api/workflows?';
            if (status) url += `status=${status}&`;
            if (priority) url += `priority=${priority}&`;
            if (assignee) url += `assignee=${assignee}&`;

            return fetch(url).then(r => r.json());
        }

        function renderWorkflows() {
            fetchWorkflows().then(data => {
                workflows = data.workflows || [];

                // Update stats
                document.getElementById('open-count').textContent = workflows.filter(w => w.status === 'open').length;
                document.getElementById('progress-count').textContent = workflows.filter(w => w.status === 'in_progress').length;
                document.getElementById('completed-count').textContent = workflows.filter(w => w.status === 'completed').length;

                // Render list
                const listEl = document.getElementById('workflow-list');
                if (workflows.length === 0) {
                    listEl.innerHTML = '<div class="empty">没有工作流</div>';
                    return;
                }

                listEl.innerHTML = workflows.map(w => `
                    <div class="workflow-card">
                        <div class="header">
                            <div class="title">${w.title}</div>
                            <div>
                                <span class="badge ${w.priority}">${w.priority.toUpperCase()}</span>
                                <span class="badge ${w.status}">${w.status}</span>
                            </div>
                        </div>
                        <div class="meta">
                            <span>类型: ${w.type}</span>
                            <span>负责人: ${w.assignee || '未认领'}</span>
                            <span>评论: ${w.comment_count || 0}</span>
                        </div>
                        <div class="description">${w.description.substring(0, 200)}...</div>
                        <div class="actions">
                            <button onclick="viewWorkflow(${w.id})">查看详情</button>
                            ${w.status === 'open' ? `<button onclick="claimWorkflow(${w.id})">认领</button>` : ''}
                        </div>
                    </div>
                `).join('');
            }).catch(err => {
                console.error('Failed to load workflows:', err);
                document.getElementById('workflow-list').innerHTML = '<div class="empty">加载失败，请刷新页面重试</div>';
            });
        }

        function refreshWorkflows() {
            renderWorkflows();
        }

        function claimWorkflow(id) {
            const assignee = prompt('请输入你的名字 (IronGate/Forge/Shadow):');
            if (!assignee) return;

            fetch(`/api/workflows/${id}/claim`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ assignee })
            }).then(r => r.json()).then(data => {
                if (data.error) {
                    alert('错误: ' + data.error);
                } else {
                    refreshWorkflows();
                }
            }).catch(err => {
                alert('网络错误: ' + err.message);
            });
        }

        function viewWorkflow(id) {
            window.open(`/api/workflows/${id}`, '_blank');
        }

        function showCreateModal() {
            const title = prompt('工作流标题:');
            if (!title) return;

            const description = prompt('工作流描述:');
            if (!description) return;

            const type = prompt('类型 (feature/bug/refactor/test/doc):', 'feature');
            const priority = prompt('优先级 (p0/p1/p2/p3):', 'p1');
            const createdBy = prompt('创建者:', 'Shadow');

            fetch('/api/workflows', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title,
                    description,
                    type,
                    priority,
                    created_by: createdBy
                })
            }).then(r => r.json()).then(data => {
                if (data.error) {
                    alert('错误: ' + data.error);
                } else {
                    alert('工作流创建成功！');
                    refreshWorkflows();
                }
            }).catch(err => {
                alert('网络错误: ' + err.message);
            });
        }

        // SSE events
        function connectEvents() {
            const ev = new EventSource('/events');
            ev.onmessage = (e) => {
                const data = JSON.parse(e.data);
                console.log('Event:', data.type, data.content);
                refreshWorkflows();
            };
            ev.onerror = () => {
                setTimeout(connectEvents, 2000);
            };
        }

        // Initial load
        renderWorkflows();
        connectEvents();
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
