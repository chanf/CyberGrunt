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

def execute(name, args, ctx):
    """Execute a limb or MCP tool and return the result"""
    log.info(f"[limb] {name}({json.dumps(args, ensure_ascii=False)[:200]})")
    
    # 1. Try local registry
    entry = Registry.get(name)
    if entry:
        try:
            return entry["fn"](args, ctx)
        except Exception as e:
            log.error(f"[limb] {name} error: {e}", exc_info=True)
            return f"[error] {e}"
            
    # 2. Try MCP
    if "__" in name:
        try:
            return mcp_client.execute(name, args)
        except Exception as e:
            log.error(f"[mcp] {name} error: {e}")
            return f"[error] MCP tool failed: {e}"
            
    return f"[error] unknown tool: {name}"

def load_all():
    """Dynamically discover and load limbs from core/, skills/, and plugins/"""
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
