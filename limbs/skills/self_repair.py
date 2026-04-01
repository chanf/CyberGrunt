"""
Self-Repair & Plugin Skill - Diagnostics and System Evolution
"""

import os
import json
import logging
import subprocess
import shutil
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple
from limbs.hub import limb
import limbs.hub as hub

log = logging.getLogger("agent")
CST = timezone(timedelta(hours=8))

# Plugin directory: root plugins/
_plugins_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "plugins")

_CLEANUP_SUFFIXES = (".log", ".tmp", ".cache", ".old", ".db-wal", ".db-shm")
_DEFAULT_DISK_THRESHOLD_MB = 1024
_DEFAULT_CLEANUP_LIMIT_MB = 256


def _project_root(ctx: Dict[str, Any]) -> str:
    workspace = ctx.get("workspace", ".")
    return os.path.abspath(os.path.dirname(workspace))


def _safe_disk_usage(path: str) -> Tuple[int, int, int]:
    usage = shutil.disk_usage(path)
    return int(usage.total), int(usage.used), int(usage.free)


def _collect_cleanup_candidates(ctx: Dict[str, Any]) -> List[Tuple[float, str, int]]:
    """Collect low-risk cleanup candidates: logs/tmp/cache/wal files."""
    workspace = ctx.get("workspace", ".")
    root = _project_root(ctx)
    scan_dirs = [
        os.path.join(root, "test_reports"),
        os.path.join(workspace, "tmp"),
        os.path.join(workspace, "forum"),
        os.path.join(root, "ai_forum", "ai_forum"),
    ]

    candidates: List[Tuple[float, str, int]] = []
    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        for base, _, files in os.walk(scan_dir):
            for name in files:
                if not name.endswith(_CLEANUP_SUFFIXES):
                    continue
                fpath = os.path.join(base, name)
                try:
                    stat = os.stat(fpath)
                except OSError:
                    continue
                candidates.append((stat.st_mtime, fpath, int(stat.st_size)))

    candidates.sort(key=lambda item: item[0])  # oldest first
    return candidates


def _cleanup_disk_if_needed(ctx: Dict[str, Any], threshold_mb: int, cleanup_limit_mb: int) -> Dict[str, Any]:
    """Delete low-risk temp/log files when free disk is below threshold."""
    workspace = ctx.get("workspace", ".")
    _, _, free_before = _safe_disk_usage(workspace)
    free_before_mb = free_before // (1024 * 1024)

    result: Dict[str, Any] = {
        "triggered": free_before_mb < threshold_mb,
        "free_before_mb": free_before_mb,
        "free_after_mb": free_before_mb,
        "deleted_count": 0,
        "reclaimed_mb": 0,
        "deleted_files": [],
        "errors": [],
    }
    if not result["triggered"]:
        return result

    budget_bytes = max(1, int(cleanup_limit_mb)) * 1024 * 1024
    reclaimed = 0
    deleted_files: List[str] = []
    errors: List[str] = []
    root = _project_root(ctx)
    for _, fpath, fsize in _collect_cleanup_candidates(ctx):
        if reclaimed >= budget_bytes:
            break
        try:
            os.remove(fpath)
            reclaimed += fsize
            deleted_files.append(os.path.relpath(fpath, root))
        except OSError as exc:
            errors.append(f"{fpath}: {exc}")

    _, _, free_after = _safe_disk_usage(workspace)
    result["free_after_mb"] = free_after // (1024 * 1024)
    result["deleted_count"] = len(deleted_files)
    result["reclaimed_mb"] = reclaimed // (1024 * 1024)
    result["deleted_files"] = deleted_files
    result["errors"] = errors
    return result


def _get_offline_mcp_servers() -> List[str]:
    """Best-effort offline check for stdio MCP servers."""
    try:
        import mcp_client
    except Exception:
        return []

    offline: List[str] = []
    for name, srv in mcp_client._servers.items():
        transport = getattr(srv, "transport", "stdio")
        if transport != "stdio":
            continue
        proc = getattr(srv, "_proc", None)
        if proc is None or proc.poll() is not None:
            offline.append(name)
    return offline


def _attempt_mcp_reconnect(force_reconnect: bool = False) -> Dict[str, Any]:
    offline_before = _get_offline_mcp_servers()
    should_attempt = force_reconnect or bool(offline_before)
    if not should_attempt:
        return {
            "attempted": False,
            "offline_before": offline_before,
            "offline_after": offline_before,
            "added": [],
            "removed": [],
            "total": len(offline_before),
            "error": "",
        }

    try:
        added, removed, total = hub.reload_mcp()
        offline_after = _get_offline_mcp_servers()
        return {
            "attempted": True,
            "offline_before": offline_before,
            "offline_after": offline_after,
            "added": sorted(list(added)),
            "removed": sorted(list(removed)),
            "total": int(total),
            "error": "",
        }
    except Exception as exc:
        return {
            "attempted": True,
            "offline_before": offline_before,
            "offline_after": _get_offline_mcp_servers(),
            "added": [],
            "removed": [],
            "total": 0,
            "error": str(exc),
        }

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


@limb(
    "self_repair_loop",
    "Run one self-repair cycle: self_check + diagnose + targeted healing "
    "(disk cleanup and MCP reconnect) + post-repair diagnose.",
    {
        "disk_free_mb_threshold": {
            "type": "integer",
            "description": "Trigger cleanup when free disk (MB) is below this threshold. Default 1024.",
        },
        "cleanup_limit_mb": {
            "type": "integer",
            "description": "Max MB to delete during one cleanup cycle. Default 256.",
        },
        "force_mcp_reconnect": {
            "type": "boolean",
            "description": "Force MCP reload even when offline status is not detected.",
        },
    },
)
def tool_self_repair_loop(args, ctx):
    threshold_mb = int(args.get("disk_free_mb_threshold", _DEFAULT_DISK_THRESHOLD_MB))
    cleanup_limit_mb = int(args.get("cleanup_limit_mb", _DEFAULT_CLEANUP_LIMIT_MB))
    force_mcp_reconnect = bool(args.get("force_mcp_reconnect", False))

    # Baseline diagnostics
    pre_check = tool_self_check({}, ctx)
    pre_diag = tool_diagnose({"target": "all"}, ctx)

    # Repair actions
    disk_result = _cleanup_disk_if_needed(ctx, threshold_mb, cleanup_limit_mb)
    mcp_result = _attempt_mcp_reconnect(force_mcp_reconnect)

    # Post diagnostics
    post_diag = tool_diagnose({"target": "all"}, ctx)

    lines = []
    lines.append("== Self Repair Loop ==")
    lines.append("Time: %s" % datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST"))
    lines.append("")
    lines.append("== Self Check (Before) ==")
    lines.append(pre_check)
    lines.append("")
    lines.append("== Diagnose (Before) ==")
    lines.append(pre_diag)
    lines.append("")
    lines.append("== Repair Actions ==")

    if disk_result["triggered"]:
        lines.append(
            "Disk cleanup triggered: free %dMB -> %dMB, deleted %d files, reclaimed %dMB."
            % (
                disk_result["free_before_mb"],
                disk_result["free_after_mb"],
                disk_result["deleted_count"],
                disk_result["reclaimed_mb"],
            )
        )
        if disk_result["deleted_files"]:
            lines.append("Deleted files:")
            for rel in disk_result["deleted_files"][:20]:
                lines.append("  - %s" % rel)
            if len(disk_result["deleted_files"]) > 20:
                lines.append("  - ... (%d more)" % (len(disk_result["deleted_files"]) - 20))
        if disk_result["errors"]:
            lines.append("Cleanup errors:")
            for err in disk_result["errors"][:10]:
                lines.append("  - %s" % err)
    else:
        lines.append(
            "Disk cleanup skipped: free %dMB >= threshold %dMB."
            % (disk_result["free_before_mb"], threshold_mb)
        )

    if mcp_result["attempted"]:
        if mcp_result["error"]:
            lines.append(
                "MCP reconnect attempted but failed: %s. offline(before)=%s offline(after)=%s"
                % (
                    mcp_result["error"],
                    ",".join(mcp_result["offline_before"]) or "none",
                    ",".join(mcp_result["offline_after"]) or "none",
                )
            )
        else:
            lines.append(
                "MCP reconnect attempted: offline(before)=%s offline(after)=%s added=%s removed=%s total=%d."
                % (
                    ",".join(mcp_result["offline_before"]) or "none",
                    ",".join(mcp_result["offline_after"]) or "none",
                    ",".join(mcp_result["added"]) or "none",
                    ",".join(mcp_result["removed"]) or "none",
                    mcp_result["total"],
                )
            )
    else:
        lines.append("MCP reconnect skipped: no offline stdio server detected.")

    lines.append("")
    lines.append("== Diagnose (After) ==")
    lines.append(post_diag)
    return "\n".join(lines)

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
