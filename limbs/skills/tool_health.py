"""Tool health report skill backed by hub metrics."""

from __future__ import annotations

from limbs.hub import limb
import limbs.hub as hub


@limb("tool_health_report", "Show current tool health score ranking.", {})
def tool_health_report(args, ctx):
    return hub.get_tool_health_report()
