"""
Notifier & Scheduler Skill - Managing tasks and rich notifications for CyberGrunt 2.0
"""

import time
from limbs.hub import limb
import messaging
import scheduler

@limb("schedule", "Create a scheduled task. One-shot tasks use delay_seconds, recurring tasks use cron_expr. "
      "On trigger, the message is sent to LLM as a user message for processing.",
      {"name": {"type": "string", "description": "Task name (unique identifier)"},
       "message": {"type": "string", "description": "Message sent to LLM on trigger"},
       "delay_seconds": {"type": "integer", "description": "Delay in seconds (one-shot task)"},
       "cron_expr": {"type": "string", "description": "Cron expression (recurring task, e.g. '0 9 * * *')"},
       "once": {"type": "boolean", "description": "Execute only once (default true, only for cron_expr)"}},
      ["name", "message"])
def tool_schedule(args, ctx):
    return scheduler.add(args)

@limb("list_schedules", "List all scheduled tasks", {})
def tool_list_schedules(args, ctx):
    return scheduler.list_all()

@limb("remove_schedule", "Delete a scheduled task",
      {"name": {"type": "string", "description": "Task name"}},
      ["name"])
def tool_remove_schedule(args, ctx):
    return scheduler.remove(args["name"])

@limb("send_image", "Send an image to the owner. Supports HTTP URL or local file path.",
      {"path": {"type": "string", "description": "Image URL or local file path"},
       "caption": {"type": "string", "description": "Optional text caption"}},
      ["path"])
def tool_send_image(args, ctx):
    # ctx['owner_id'] is injected by Brain
    result = messaging.upload_and_send(ctx["owner_id"], args["path"], args.get("caption", ""), ctx["workspace"])
    return "Image sent" if result.get("code") == 0 else f"[error] {result.get('msg')}"

@limb("send_link", "Send a rich link card to the owner.",
      {"title": {"type": "string", "description": "Card title"},
       "desc": {"type": "string", "description": "Card description"},
       "link_url": {"type": "string", "description": "Click-through URL"},
       "icon_url": {"type": "string", "description": "Card icon URL"}},
      ["title", "desc", "link_url"])
def tool_send_link(args, ctx):
    result = messaging.send_link(ctx["owner_id"], args["title"], args["desc"], args["link_url"], args.get("icon_url", ""))
    return "Link sent" if result.get("code") == 0 else f"[error] {result.get('msg')}"
