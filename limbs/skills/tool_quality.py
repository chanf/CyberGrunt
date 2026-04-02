"""Tool quality score reporting skills."""

from __future__ import annotations

from typing import Any, Dict

from limbs.hub import limb
from brain import tool_quality as tq


@limb(
    "tool_quality_report",
    "Show tool quality scores: calls, success rate, and experimental status.",
    {
        "limit": {
            "type": "integer",
            "description": "Number of tools to show, default 20, max 200.",
        }
    },
)
def tool_quality_report(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    _ = ctx
    try:
        limit = int(args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 200))
    rows = tq.list_tools(limit=limit)
    if not rows:
        return "No tool quality records yet."

    lines = ["Tool quality report:"]
    for row in rows:
        tag = "experimental" if row["experimental"] else "stable"
        lines.append(
            "- {name}: calls={calls}, success_rate={rate:.2f}, failures={fail}, blocked={blocked}, status={tag}".format(
                name=row["tool_name"],
                calls=row["calls"],
                rate=row["success_rate"],
                fail=row["failures"],
                blocked=row["blocked"],
                tag=tag,
            )
        )
    return "\n".join(lines)
