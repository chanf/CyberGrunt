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

_registry = {}  # name -> {"fn", "definition"}
_extra_config = {}

def limb(name, description, properties, required=None):
    """Decorator: register a function as a limb (tool)"""
    def decorator(fn):
        _registry[name] = {
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
        }
        return fn
    return decorator

# Alias for backward compatibility
tool = limb

def get_definitions():
    """Return all registered limb definitions including MCP tools"""
    defs = [entry["definition"] for entry in _registry.values()]
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
    entry = _registry.get(name)
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
    """Dynamically discover and load limbs from core/ and skills/"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    for category in ["core", "skills", "mcp"]:
        cat_dir = os.path.join(base_dir, category)
        if not os.path.exists(cat_dir):
            continue
            
        for item in os.listdir(cat_dir):
            if item.startswith("__") or item.startswith("."):
                continue
                
            module_name = None
            file_path = None
            
            if item.endswith(".py"):
                module_name = f"limbs.{category}.{item[:-3]}"
                file_path = os.path.join(cat_dir, item)
            elif os.path.isdir(os.path.join(cat_dir, item)):
                if os.path.exists(os.path.join(cat_dir, item, "__init__.py")):
                    module_name = f"limbs.{category}.{item}"
                    file_path = os.path.join(cat_dir, item, "__init__.py")
            
            if module_name and file_path:
                try:
                    spec = importlib.util.spec_from_file_location(module_name, file_path)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    log.info(f"[hub] Loaded {category} limb: {module_name}")
                except Exception as e:
                    log.error(f"[hub] Failed to load {module_name}: {e}")

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
    # This will be called by the reload_mcp limb
    added, removed, total = mcp_client.reload(_extra_config)
    return added, removed, total
