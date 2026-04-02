"""
Search Skill - Web search for CyberGrunt 2.0
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, Union

from limbs.hub import limb


def _get_tavily_api_key() -> str:
    """Read Tavily API key from hub runtime config."""
    from limbs.hub import _extra_config

    return str(_extra_config.get("tavily_api_key") or "").strip()


def _call_tavily(query: str) -> Union[Dict[str, Any], str]:
    """Call Tavily search API and return JSON payload or error string."""
    api_key = _get_tavily_api_key()
    if not api_key:
        return "[error] Tavily API key not configured."

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "include_answer": True,
        "max_results": 5,
    }
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return f"[error] search failed: {exc}"


@limb(
    "web_search",
    "Search the web for real-time information, news, or complex questions.",
    {"query": {"type": "string", "description": "Search query"}},
    ["query"],
)
def tool_web_search(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """Execute web search and format answer + citations for the model."""
    query = str(args.get("query") or "").strip()
    if not query:
        return "[error] query is required"

    result = _call_tavily(query)
    if isinstance(result, str) and result.startswith("[error]"):
        return result
    if not isinstance(result, dict):
        return "[error] search failed: invalid response payload"

    answer = str(result.get("answer") or "").strip()
    rows = result.get("results") or []

    lines = []
    if answer:
        lines.append(f"AI Summary: {answer}\n")

    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "(no title)")
        url = str(row.get("url") or "(no url)")
        content = str(row.get("content") or "")
        lines.append(f"- {title}\n  URL: {url}\n  Snippet: {content[:300]}")

    return "\n".join(lines) or "No relevant search results found."
