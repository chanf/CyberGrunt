"""
Memory Skill - Keyword search and semantic recall for CyberGrunt 2.0
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict

from limbs.hub import limb

@limb("search_memory", "Search memory files. Uses keyword search in workspace/memory/ directory.",
      {"query": {"type": "string", "description": "Search keywords (space-separated)"},
       "scope": {"type": "string", "description": "Search scope: all (default), long (MEMORY.md only), daily (daily logs only)"}},
      ["query"])
def tool_search_memory(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    query = str(args["query"])
    scope = str(args.get("scope", "all"))
    memory_dir = os.path.join(ctx["workspace"], "memory")

    if not os.path.isdir(memory_dir):
        return "Memory directory does not exist."

    grep_args = ["grep", "-r", "-i", "-n", "--include=*.md"]
    if scope == "long":
        target = os.path.join(memory_dir, "MEMORY.md")
        if not os.path.exists(target):
            return "MEMORY.md does not exist."
        grep_args = ["grep", "-i", "-n", "--", query, target]
    elif scope == "daily":
        grep_args.extend(["--include=2*.md", "--", query, memory_dir])
    else:
        grep_args.extend(["--", query, memory_dir])

    try:
        result = subprocess.run(grep_args, capture_output=True, text=True, timeout=10)
        output = result.stdout.strip()
        if not output:
            return "No memories found containing '%s'." % query

        lines = output.split("\n")
        if len(lines) > 30:
            return "\n".join(lines[:30]) + ("\n... %d total matches, showing first 30" % len(lines))
        return "%d matches:\n%s" % (len(lines), "\n".join(lines))
    except Exception as e:
        return "[error] Search failed: %s" % e

@limb("recall", "Semantic search in long-term memory. Recall historical facts or previous conversations.",
      {"query": {"type": "string", "description": "Search keywords or question"},
       "scope": {"type": "string", "description": "Namespace scope: auto (default), public, context, qa, dev, ops"}},
      ["query"])
def tool_recall(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    from brain import memory as mem_mod
    result = mem_mod.retrieve(
        str(args["query"]),
        str(ctx["session_key"]),
        top_k=5,
        scope=args.get("scope", "auto"),
    )
    return result or 'No relevant memories found.'
