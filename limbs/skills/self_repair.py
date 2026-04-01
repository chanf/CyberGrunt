"""
Self-Repair & Plugin Skill - Diagnostics and System Evolution
"""

import os
import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from limbs.hub import limb
import limbs.hub as hub

log = logging.getLogger("agent")
CST = timezone(timedelta(hours=8))

# Plugin directory: root plugins/
_plugins_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "plugins")

@limb("self_check", "System self-check: collect today's conversation stats, system health, "
      "error logs, scheduled task status, etc. Used to generate daily self-check reports.", {})
def tool_self_check(args, ctx):
    now = datetime.now(CST)
    today = now.strftime("%Y-%m-%d")
    report = []

    # 1. Today's active sessions
    sessions_dir = os.path.join(os.path.dirname(ctx["workspace"]), "sessions")
    active_sessions = 0
    total_user_msgs = 0
    total_assistant_msgs = 0
    total_tool_calls = 0
    if os.path.isdir(sessions_dir):
        for fname in os.listdir(sessions_dir):
            if not fname.endswith(".json"): continue
            fpath = os.path.join(sessions_dir, fname)
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath), CST)
            if mtime.strftime("%Y-%m-%d") == today:
                active_sessions += 1
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        msgs = json.load(f)
                    for m in msgs:
                        if m.get("role") == "user": total_user_msgs += 1
                        elif m.get("role") == "assistant":
                            total_assistant_msgs += 1
                            if m.get("tool_calls"): total_tool_calls += len(m["tool_calls"])
                except Exception: pass
    report.append("== Today's Conversations (%s) ==" % today)
    report.append("Active sessions: %d" % active_sessions)
    report.append("User messages: %d, Assistant replies: %d, Tool calls: %d" % (total_user_msgs, total_assistant_msgs, total_tool_calls))

    # 2. Error Logs (Generic check)
    report.append("\n== System Status ==")
    try:
        mem = subprocess.run(["bash", "-c", "free -h | grep Mem"], capture_output=True, text=True, timeout=5).stdout.strip()
        disk = subprocess.run(["bash", "-c", "df -h . | tail -1"], capture_output=True, text=True, timeout=5).stdout.strip()
        report.append("Memory: %s" % mem)
        report.append("Disk: %s" % disk)
    except Exception: pass

    # 3. Scheduled task status
    try:
        jobs_file = os.path.join(os.path.dirname(ctx["workspace"]), "jobs.json")
        if os.path.exists(jobs_file):
            with open(jobs_file, "r", encoding="utf-8") as f:
                jobs = json.load(f)
            report.append("\n== Scheduled Tasks (%d) ==" % len(jobs))
            for j in jobs:
                cron = j.get("cron_expr", "once")
                last = j.get("last_run")
                last_str = datetime.fromtimestamp(last, CST).strftime("%H:%M") if last else "never"
                report.append("  - %s (%s) last: %s" % (j["name"], cron, last_str))
    except Exception: pass

    return "\n".join(report)

@limb("diagnose", "Diagnose system problems. Check session file health, MCP server status, error logs.",
      {"target": {"type": "string", "description": "Diagnosis target: 'session', 'mcp', 'all'"}},
      ["target"])
def tool_diagnose(args, ctx):
    target = args.get("target", "all")
    report = []
    
    if target in ("session", "all"):
        report.append("== Session File Health Check ==")
        sessions_dir = os.path.join(os.path.dirname(ctx["workspace"]), "sessions")
        if os.path.isdir(sessions_dir):
            for fname in sorted(os.listdir(sessions_dir)):
                if not fname.endswith(".json"): continue
                fpath = os.path.join(sessions_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        msgs = json.load(f)
                    total_bytes = sum(len(json.dumps(m)) for m in msgs)
                    report.append("  %s: %d msgs, %d bytes" % (fname, len(msgs), total_bytes))
                except Exception as e:
                    report.append("  %s: read failed (%s)" % (fname, e))
    
    if target in ("mcp", "all"):
        report.append("\n== MCP Server Status ==")
        import mcp_client
        for name, srv in mcp_client._servers.items():
            alive = "running" if (srv._proc and srv._proc.poll() is None) else "http/external"
            report.append("  %s: %d tools, %s" % (name, len(srv._tools), alive))
            
    return "\n".join(report)

@limb("create_tool", "Create a new custom tool plugin. Code is hot-loaded immediately. Persists across restarts. Use @limb decorator in code to register tools.",
      {"name": {"type": "string", "description": "Tool name (e.g. 'weather')"},
       "code": {"type": "string", "description": "Complete Python code with @limb decorator"}},
      ["name", "code"])
def tool_create_tool(args, ctx):
    name = args["name"]
    code = args["code"]
    
    if not name.replace("_", "").isalnum():
        return "[error] Invalid tool name (letters, digits, underscores only)"
    
    os.makedirs(_plugins_dir, exist_ok=True)
    fpath = os.path.join(_plugins_dir, f"{name}.py")
    
    try:
        # Save code
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(code)
        
        # Trigger Hot-reload in hub
        hub.load_all()
        
        # Verify if it was actually registered
        if hub.Registry.get(name):
            return f"Custom tool '{name}' created and hot-loaded successfully."
        else:
            return f"Tool code saved to {fpath}, but failed to register. Check logs for syntax errors."
    except Exception as e:
        return f"[error] Failed to create tool: {e}"

@limb("list_custom_tools", "List all custom tool plugins in plugins/ directory", {})
def tool_list_custom_tools(args, ctx):
    if not os.path.isdir(_plugins_dir):
        return "No custom tools directory found."
    
    plugins = [f for f in sorted(os.listdir(_plugins_dir)) if f.endswith(".py")]
    if not plugins:
        return "No custom tools yet."
    
    lines = ["Custom tools (%d):" % len(plugins)]
    for fname in plugins:
        tool_name = fname[:-3]
        fpath = os.path.join(_plugins_dir, fname)
        size = os.path.getsize(fpath)
        status = "active" if hub.Registry.get(tool_name) else "error/not loaded"
        lines.append(f"  - {tool_name} ({status}, {size} bytes)")
    return "\n".join(lines)

@limb("remove_tool", "Delete a custom tool plugin. Persists across restarts.",
      {"name": {"type": "string", "description": "Tool name to delete"}},
      ["name"])
def tool_remove_tool(args, ctx):
    name = args["name"]
    fpath = os.path.join(_plugins_dir, f"{name}.py")
    
    if not os.path.exists(fpath):
        return f"[error] Custom tool '{name}' not found."
    
    try:
        os.remove(fpath)
        # Registry update happens during next load or manually here
        if hub.Registry.get(name):
            # Since we can't easily "unregister" without a Registry.delete method
            # We'll just rely on Registry.clear() if needed, but for now 
            # let's just delete from internal data
            del hub.Registry._data[name]
        return f"Deleted custom tool '{name}'."
    except Exception as e:
        return f"[error] Failed to remove tool: {e}"

@limb("reload_mcp", "Hot-reload MCP servers from config.json", {})
def tool_reload_mcp(args, ctx):
    added, removed, total = hub.reload_mcp()
    return f"MCP Reloaded: Added {len(added)}, Removed {len(removed)}, Total {total}"
