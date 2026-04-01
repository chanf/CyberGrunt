"""
Limb Hub - Discovery and Dispatcher for CyberGrunt 2.0
Scans limbs/ directory and registers tools for the Brain.
"""

import os
import json
import logging
import importlib.util
import sys

log = logging.getLogger("agent")

_registry = {}  # name -> {"fn", "definition"}

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
    """Return all registered limb definitions"""
    return [entry["definition"] for entry in _registry.values()]

def execute(name, args, ctx):
    """Execute a limb and return the result"""
    log.info(f"[limb] {name}({json.dumps(args, ensure_ascii=False)[:200]})")
    entry = _registry.get(name)
    if not entry:
        return f"[error] unknown limb: {name}"
    try:
        return entry["fn"](args, ctx)
    except Exception as e:
        log.error(f"[limb] {name} error: {e}", exc_info=True)
        return f"[error] {e}"

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
                # Handle package skills
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

# Configuration injection for limbs
_extra_config = {}

def init_extra(config):
    global _extra_config
    _extra_config = config
    # Load limbs after config is available
    load_all()
