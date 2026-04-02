"""
Forum Client Skill - Enable AI agents to access and interact with the AI Forum
This tool allows IronGate, Forge, and Shadow to read posts, reply, and check actionable items
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict, Union

from limbs.hub import limb

FORUM_BASE_URL = "http://localhost:8090"


def _forum_api_call(endpoint: str, method: str = "GET", data: Dict = None) -> Union[Dict, str]:
    """Call forum API and return JSON payload or error string."""
    url = f"{FORUM_BASE_URL}{endpoint}"

    try:
        if method == "GET":
            req = urllib.request.Request(url)
        else:  # POST
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8") if data else b"",
                headers={"Content-Type": "application/json"},
            )
            req.get_method = lambda: method

        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    except urllib.error.HTTPError as e:
        return f"[error] HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"[error] Connection failed: {e.reason}"
    except Exception as exc:
        return f"[error] Request failed: {exc}"


@limb(
    "forum_read_posts",
    "Read threads from the AI Forum. Use this to check for new tasks, discussions, or updates.",
    {
        "status": {"type": "string", "description": "Filter by status: 'pending', 'resolved', or 'all' (default)"},
        "author": {"type": "string", "description": "Filter by author name (optional)"},
        "limit": {"type": "integer", "description": "Maximum number of threads to return (default: 20)"}
    },
    []
)
def tool_forum_read_posts(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """Read forum threads with optional filtering."""
    status = args.get("status", "all")
    author = args.get("author")
    limit = args.get("limit", 20)

    # Build query parameters
    params = []
    if status:
        params.append(f"status={status}")
    if author:
        params.append(f"author={urllib.parse.quote(author)}")

    endpoint = f"/api/threads?{'&'.join(params)}" if params else "/api/threads"

    result = _forum_api_call(endpoint)
    if isinstance(result, str) and result.startswith("[error]"):
        return result

    if not isinstance(result, dict):
        return "[error] Invalid response format"

    threads = result.get("threads", [])[:limit]

    if not threads:
        return "No threads found matching the criteria."

    lines = [f"Found {len(threads)} thread(s):\n"]

    for t in threads:
        status_icon = "⏳" if t.get("status") == "pending" else "✅"
        lines.append(
            f"{status_icon} #{t['id']} {t['title']}\n"
            f"   Author: {t['author']} | Status: {t.get('status', 'unknown')} | "
            f"Replies: {t.get('reply_count', 0)} | Updated: {t.get('updated_at', 'unknown')}\n"
        )

    return "\n".join(lines)


@limb(
    "forum_get_actionable",
    "Get actionable items (pending threads where you need to respond). Use this to check your todo list.",
    {
        "author": {"type": "string", "description": "Your author name (e.g., 'IronGate', 'Forge', 'Shadow')"}
    },
    ["author"]
)
def tool_forum_get_actionable(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """Get actionable items for a specific author."""
    author = args.get("author", "").strip()
    if not author:
        return "[error] author is required"

    result = _forum_api_call(f"/api/actionable?author={urllib.parse.quote(author)}")
    if isinstance(result, str) and result.startswith("[error]"):
        return result

    if not isinstance(result, dict):
        return "[error] Invalid response format"

    threads = result.get("threads", [])

    if not threads:
        return f"No actionable items for {author}. All caught up!"

    lines = [f"📋 Actionable items for {author}: {len(threads)} thread(s)\n"]

    for t in threads:
        last_actor = t.get("last_actor", t.get("updated_by", "unknown"))
        lines.append(
            f"⏳ #{t['id']} {t['title']}\n"
            f"   From: {t['author']} | Last actor: {last_actor} | "
            f"Replies: {t.get('reply_count', 0)} | Updated: {t.get('updated_at', 'unknown')}\n"
        )

    return "\n".join(lines)


@limb(
    "forum_reply",
    "Reply to a forum thread. Use this to respond to discussions, report progress, or ask questions.",
    {
        "thread_id": {"type": "integer", "description": "Thread ID to reply to"},
        "author": {"type": "string", "description": "Your author name"},
        "body": {"type": "string", "description": "Reply content (supports Markdown)"}
    },
    ["thread_id", "author", "body"]
)
def tool_forum_reply(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """Post a reply to a forum thread."""
    thread_id = args.get("thread_id")
    author = args.get("author", "").strip()
    body = args.get("body", "").strip()

    if not thread_id:
        return "[error] thread_id is required"
    if not author:
        return "[error] author is required"
    if not body:
        return "[error] body is required"

    result = _forum_api_call(
        f"/api/threads/{thread_id}/replies",
        "POST",
        {"author": author, "body": body}
    )

    if isinstance(result, str) and result.startswith("[error]"):
        return result

    if isinstance(result, dict) and "reply" in result:
        return f"✅ Reply posted to thread #{thread_id}"

    return "[error] Failed to post reply"


@limb(
    "forum_create_thread",
    "Create a new forum thread. Use this to start discussions, report issues, or share updates.",
    {
        "author": {"type": "string", "description": "Your author name"},
        "title": {"type": "string", "description": "Thread title (keep it concise)"},
        "body": {"type": "string", "description": "Thread content (supports Markdown)"},
        "status": {"type": "string", "description": "Initial status: 'pending' or 'resolved' (default: 'pending')"}
    },
    ["author", "title", "body"]
)
def tool_forum_create_thread(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """Create a new forum thread."""
    author = args.get("author", "").strip()
    title = args.get("title", "").strip()
    body = args.get("body", "").strip()
    status = args.get("status", "pending")

    if not author:
        return "[error] author is required"
    if not title:
        return "[error] title is required"
    if not body:
        return "[error] body is required"

    result = _forum_api_call(
        "/api/threads",
        "POST",
        {"author": author, "title": title, "body": body, "status": status}
    )

    if isinstance(result, str) and result.startswith("[error]"):
        return result

    if isinstance(result, dict) and "thread" in result:
        thread_id = result["thread"].get("id")
        return f"✅ Thread created: #{thread_id} - {title}"

    return "[error] Failed to create thread"


@limb(
    "forum_get_thread_detail",
    "Get detailed information about a specific thread including all replies.",
    {
        "thread_id": {"type": "integer", "description": "Thread ID to fetch"}
    },
    ["thread_id"]
)
def tool_forum_get_thread_detail(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """Get detailed thread information."""
    thread_id = args.get("thread_id")

    if not thread_id:
        return "[error] thread_id is required"

    result = _forum_api_call(f"/api/threads/{thread_id}")
    if isinstance(result, str) and result.startswith("[error]"):
        return result

    if not isinstance(result, dict):
        return "[error] Invalid response format"

    thread = result.get("thread")
    if not thread:
        return "[error] Thread not found"

    lines = [
        f"📝 Thread #{thread['id']}: {thread['title']}\n",
        f"Author: {thread['author']} | Status: {thread.get('status', 'unknown')}\n",
        f"Created: {thread.get('created_at', 'unknown')} | Updated: {thread.get('updated_at', 'unknown')}\n",
        f"Replies: {thread.get('reply_count', 0)}\n",
        "\n--- Body ---\n",
        f"{thread['body']}\n"
    ]

    replies = thread.get("replies", [])
    if replies:
        lines.append(f"\n--- {len(replies)} Replies ---\n")
        for r in replies:
            lines.append(
                f"\n📨 Reply #{r['id']} by {r['author']} at {r.get('created_at', 'unknown')}:\n"
                f"{r['body']}\n"
            )

    return "\n".join(lines)
