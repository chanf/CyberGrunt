"""
Limb Hub - Discovery and Dispatcher for CyberGrunt 2.0
Scans limbs/ directory and registers tools for the Brain.
Integrated with MCP (Model Context Protocol).
"""

import os
import json
import logging
import importlib.util
import sys
import time
import threading
from datetime import datetime, timezone
import mcp_client

log = logging.getLogger("agent")

# Singleton-like registry to prevent accidental overwrites during hot-reloads
class Registry:
    _data = {}
    
    @classmethod
    def set(cls, name, entry):
        cls._data[name] = entry
        
    @classmethod
    def get(cls, name):
        return cls._data.get(name)
        
    @classmethod
    def items(cls):
        return cls._data.items()
    
    @classmethod
    def clear(cls):
        cls._data.clear()

_extra_config = {}
_loaded_mtimes = {} # path -> mtime

# Directories
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(PROJECT_ROOT, "plugins")

_metrics_lock = threading.Lock()
_tool_metrics = {}
_metrics_loaded_path = ""
_metrics_dirty = False
_last_metrics_flush_ts = 0.0
_METRICS_FLUSH_INTERVAL_SEC = 1.0

def limb(name, description, properties, required=None):
    """Decorator: register a function as a limb (tool)"""
    def decorator(fn):
        Registry.set(name, {
            "fn": fn,
            "definition": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        **({"required": required} if required else {}),
                    },
                },
            },
        })
        return fn
    return decorator

# Alias for backward compatibility
tool = limb

def get_definitions():
    """Return all registered limb definitions including MCP tools"""
    defs = [entry["definition"] for name, entry in Registry.items()]
    # Add MCP tools
    try:
        defs.extend(mcp_client.get_all_tool_defs())
    except Exception as e:
        log.error(f"[hub] Failed to get MCP definitions: {e}")
    return defs


def reset_tool_metrics():
    """Reset in-memory metrics cache (primarily for tests)."""
    global _tool_metrics, _metrics_loaded_path, _metrics_dirty, _last_metrics_flush_ts
    with _metrics_lock:
        _tool_metrics = {}
        _metrics_loaded_path = ""
        _metrics_dirty = False
        _last_metrics_flush_ts = 0.0


def flush_tool_metrics():
    with _metrics_lock:
        if _metrics_loaded_path:
            _maybe_flush_metrics(_metrics_loaded_path, force=True)


def get_tool_metrics(limit=200):
    limit = max(1, min(int(limit), 500))
    with _metrics_lock:
        rows = sorted(
            _tool_metrics.values(),
            key=lambda x: (x.get("last_used_at", ""), x.get("tool_name", "")),
            reverse=True,
        )
    return rows[:limit]


def get_tool_health_report():
    rows = get_tool_metrics(limit=500)
    if not rows:
        return "No tool metrics yet."
    lines = ["Tool Health Report:"]
    # Score descending
    rows = sorted(rows, key=lambda x: x.get("score", 0.0), reverse=True)
    for row in rows:
        lines.append(
            "- {name}: score={score:.1f}, invoke={invoke}, success={succ}, avg={avg:.1f}ms, status={status}".format(
                name=row["tool_name"],
                score=row["score"],
                invoke=row["invoke_count"],
                succ=row["success_count"],
                avg=row["avg_duration_ms"],
                status="experimental" if row.get("experimental") else "stable",
            )
        )
    return "\n".join(lines)


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _metrics_file_from_ctx(ctx):
    workspace = os.path.abspath(ctx.get("workspace", os.path.join(PROJECT_ROOT, "workspace")))
    return os.path.join(workspace, "files", "tool_metrics.json")


def _load_metrics_if_needed(path):
    global _tool_metrics, _metrics_loaded_path, _metrics_dirty
    if _metrics_loaded_path == path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _tool_metrics = data
            else:
                _tool_metrics = {}
        except Exception:
            _tool_metrics = {}
    else:
        _tool_metrics = {}
    _metrics_loaded_path = path
    _metrics_dirty = False


def _save_metrics(path):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(_tool_metrics, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _maybe_flush_metrics(path, force=False):
    global _metrics_dirty, _last_metrics_flush_ts
    if not _metrics_dirty and not force:
        return
    now = time.time()
    if (not force) and (now - _last_metrics_flush_ts < _METRICS_FLUSH_INTERVAL_SEC):
        return
    _save_metrics(path)
    _metrics_dirty = False
    _last_metrics_flush_ts = now


def _score_from_metric(metric):
    invoke = max(int(metric.get("invoke_count", 0)), 1)
    success = int(metric.get("success_count", 0))
    avg_ms = float(metric.get("avg_duration_ms", 0.0))
    score = (float(success) / float(invoke)) * 100.0
    if avg_ms > 5000.0:
        score -= 10.0
    return max(score, 0.0)


def _update_tool_metric(name, ok, duration_ms, ctx):
    global _metrics_dirty
    path = _metrics_file_from_ctx(ctx)
    with _metrics_lock:
        _load_metrics_if_needed(path)
        metric = _tool_metrics.get(name, {
            "tool_name": name,
            "invoke_count": 0,
            "success_count": 0,
            "avg_duration_ms": 0.0,
            "last_used_at": "",
            "score": 0.0,
            "experimental": False,
        })

        invoke = int(metric.get("invoke_count", 0)) + 1
        success = int(metric.get("success_count", 0)) + (1 if ok else 0)
        prev_avg = float(metric.get("avg_duration_ms", 0.0))
        avg_ms = duration_ms if invoke == 1 else ((prev_avg * (invoke - 1)) + duration_ms) / invoke

        metric["invoke_count"] = invoke
        metric["success_count"] = success
        metric["avg_duration_ms"] = round(avg_ms, 3)
        metric["last_used_at"] = _now_iso()
        metric["score"] = round(_score_from_metric(metric), 3)
        metric["experimental"] = bool(metric["invoke_count"] > 5 and metric["score"] < 60.0)
        _tool_metrics[name] = metric
        _metrics_dirty = True
        _maybe_flush_metrics(path, force=False)
        return dict(metric)

def execute(name, args, ctx):
    """Execute a limb or MCP tool and return the result"""
    log.info(f"[limb] {name}({json.dumps(args, ensure_ascii=False)[:200]})")
    t0 = time.perf_counter()
    ok = False
    result = None

    # 1. Try local registry
    entry = Registry.get(name)
    if entry:
        try:
            result = entry["fn"](args, ctx)
            ok = not (isinstance(result, str) and result.strip().startswith("[error]"))
        except Exception as e:
            log.error(f"[limb] {name} error: {e}", exc_info=True)
            result = f"[error] {e}"
            ok = False
    # 2. Try MCP
    elif "__" in name:
        try:
            result = mcp_client.execute(name, args)
            ok = not (isinstance(result, str) and result.strip().startswith("[error]"))
        except Exception as e:
            log.error(f"[mcp] {name} error: {e}")
            result = f"[error] MCP tool failed: {e}"
            ok = False
    else:
        result = f"[error] unknown tool: {name}"
        ok = False

    # Metrics interceptor
    try:
        duration_ms = (time.perf_counter() - t0) * 1000.0
        metric = _update_tool_metric(name, ok, duration_ms, ctx)
        if (not ok) and metric.get("experimental"):
            warning = (
                f"[warning] tool '{name}' is highly unstable (score={metric.get('score', 0):.1f}). "
                "建议重写或弃用。"
            )
            if isinstance(result, str):
                result = result + "\n" + warning
            else:
                result = str(result) + "\n" + warning
    except Exception as e:
        log.error(f"[hub] metric interceptor failed for {name}: {e}")

    return result

def load_all():
    """Dynamically discover and load limbs from core/, skills/, and plugins/"""
    # If registry was cleared by tests/self-repair, we must invalidate mtime cache,
    # otherwise unchanged files would be skipped and tools disappear from registry.
    if not list(Registry.items()):
        _loaded_mtimes.clear()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. Load internal limbs (core, skills, mcp)
    for category in ["core", "skills", "mcp"]:
        cat_dir = os.path.join(base_dir, category)
        if os.path.exists(cat_dir):
            _load_from_dir(cat_dir, f"limbs.{category}")

    # 2. Load external plugins from root /plugins directory
    if os.path.exists(PLUGINS_DIR):
        _load_from_dir(PLUGINS_DIR, "plugins", is_plugin=True)

def _load_from_dir(directory, package_prefix, is_plugin=False):
    """Scan directory and load python modules."""
    for item in os.listdir(directory):
        if item.startswith("__") or item.startswith(".") or not item.endswith(".py"):
            continue
            
        file_path = os.path.join(directory, item)
        mtime = os.path.getmtime(file_path)
        
        # Incremental loading: skip if not changed
        if _loaded_mtimes.get(file_path) == mtime:
            continue
            
        module_name = f"{package_prefix}.{item[:-3]}"
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
            
            # Use a fresh namespace for each load to guarantee reload
            namespace = {
                "__name__": module_name,
                "__file__": file_path,
                "limb": limb,
                "tool": limb,
                "log": log,
                "os": os,
                "json": json,
                "sys": sys
            }
            
            # Execute the code in the namespace
            exec(compile(code, file_path, 'exec'), namespace)
            
            _loaded_mtimes[file_path] = mtime
            log.info(f"[hub] Loaded {module_name} (Hot-reload: {file_path in _loaded_mtimes})")
        except Exception as e:
            log.error(f"[hub] Failed to load {module_name}: {e}", exc_info=True)

def init_extra(config):
    """Initialize with config and load all limbs + MCP servers"""
    global _extra_config
    _extra_config = config
    # 1. Load local limbs
    load_all()
    # 2. Initialize MCP
    try:
        mcp_client.init(config)
        log.info("[hub] MCP client initialized")
    except Exception as e:
        log.error(f"[hub] MCP init failed: {e}")

def reload_mcp():
    """Hot-reload MCP configuration"""
    added, removed, total = mcp_client.reload(_extra_config)
    return added, removed, total
