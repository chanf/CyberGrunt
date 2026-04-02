"""Tool health report skill backed by hub metrics."""

from __future__ import annotations

from typing import Any, Dict

from limbs.hub import limb
import limbs.hub as hub


@limb("tool_health_report", "Show current tool health score ranking.", {})
def tool_health_report(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    _ = args
    _ = ctx
    try:
        return hub.get_tool_health_report()
    except Exception as e:
        return f"[error] failed to get tool health report: {e}"
