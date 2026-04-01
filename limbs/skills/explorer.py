"""
Explorer Skill - Web search and memory recall for CyberGrunt 2.0
"""

import os
import json
import urllib.request
import urllib.parse
from limbs.hub import limb

def _call_tavily(query):
    # Dynamic import to get config injected by main.py
    from limbs.hub import _extra_config
    api_key = _extra_config.get("tavily_api_key")
    if not api_key:
        return "[error] Tavily API key not configured."
    
    url = "https://api.tavily.com/search"
    data = json.dumps({
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "include_answer": True,
        "max_results": 5
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return f"[error] search failed: {e}"

@limb("web_search", "Search the web for real-time information, news, or complex questions.",
      {"query": {"type": "string", "description": "Search query"}},
      ["query"])
def tool_web_search(args, ctx):
    result = _call_tavily(args["query"])
    if isinstance(result, str) and result.startswith("[error]"):
        return result
    
    answer = result.get("answer", "")
    results = result.get("results", [])
    
    output = []
    if answer:
        output.append(f"AI Summary: {answer}\n")
    
    for r in results:
        output.append(f"- {r['title']}\n  URL: {r['url']}\n  Snippet: {r['content'][:300]}")
    
    return "\n".join(output) or "No relevant search results found."

@limb("recall", "Semantic search in long-term memory. Recall historical facts or previous conversations.",
      {"query": {"type": "string", "description": "Search keywords or question"}},
      ["query"])
def tool_recall(args, ctx):
    from brain import memory as mem_mod
    result = mem_mod.retrieve(args['query'], ctx['session_key'], top_k=5)
    return result or 'No relevant memories found.'
