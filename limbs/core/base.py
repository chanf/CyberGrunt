"""
Core Limbs - Essential system and file operations for CyberGrunt 2.0
"""

import os
import json
import subprocess
import time
import logging
from limbs.hub import limb
import messaging
import scheduler

log = logging.getLogger("agent")

def _resolve_path(path, workspace):
    if os.path.isabs(path):
        return path
    return os.path.join(workspace, path)

def _split_message(text, max_bytes=1800):
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        test = current + "\n" + line if current else line
        if len(test.encode("utf-8")) > max_bytes:
            if current:
                chunks.append(current)
            current = line
        else:
            current = test
    if current:
        chunks.append(current)
    return chunks

@limb("exec", "Execute a shell command on the server. "
      "Default timeout 60s. Set timeout to 300 for slow operations (installs, downloads).",
      {"command": {"type": "string", "description": "Shell command to execute"},
       "timeout": {"type": "integer", "description": "Timeout in seconds, default 60, max 300"}},
      ["command"])
def tool_exec(args, ctx):
    timeout = min(args.get("timeout", 60), 300)
    try:
        result = subprocess.run(
            args["command"], shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=ctx["workspace"]
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n[stderr] " + result.stderr) if output else result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "[error] command timed out (%ds)" % timeout

@limb("message", "Send a text message to the owner via messaging platform. "
      "Used for scheduled task notifications. Normal conversation replies don't need this tool.",
      {"content": {"type": "string", "description": "Message content"}},
      ["content"])
def tool_message(args, ctx):
    owner_id = ctx["owner_id"]
    chunks = _split_message(args["content"], 1800)
    for i, chunk in enumerate(chunks):
        messaging.send_text(owner_id, chunk)
        if i < len(chunks) - 1:
            time.sleep(0.5)
    return f"Sent to owner ({len(chunks)} messages)"

@limb("read_file", "Read file content. Path relative to workspace directory.",
      {"path": {"type": "string", "description": "File path (relative to workspace or absolute)"}},
      ["path"])
def tool_read_file(args, ctx):
    fpath = _resolve_path(args["path"], ctx["workspace"])
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > 10000:
            content = content[:10000] + f"\n... (truncated, total {len(content)} chars)"
        return content or "(empty file)"
    except FileNotFoundError:
        return f"[error] file not found: {fpath}"
    except Exception as e:
        return f"[error] {e}"

@limb("write_file", "Write file (overwrite). Path relative to workspace directory.",
      {"path": {"type": "string", "description": "File path"},
       "content": {"type": "string", "description": "File content"}},
      ["path", "content"])
def tool_write_file(args, ctx):
    fpath = _resolve_path(args["path"], ctx["workspace"])
    try:
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(args["content"])
        return f"Written to {fpath} ({len(args['content'])} chars)"
    except Exception as e:
        return f"[error] {e}"

@limb("edit_file", "Edit file: replace old text with new text. "
      "For appending, use the end of file as old and old+new content as new.",
      {"path": {"type": "string", "description": "File path"},
       "old": {"type": "string", "description": "Original text to replace"},
       "new": {"type": "string", "description": "Replacement text"}},
      ["path", "old", "new"])
def tool_edit_file(args, ctx):
    fpath = _resolve_path(args["path"], ctx["workspace"])
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        if args["old"] not in content:
            return f"[error] old string not found in {fpath}"
        content = content.replace(args["old"], args["new"], 1)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Edited {fpath}"
    except FileNotFoundError:
        return f"[error] file not found: {fpath}"
    except Exception as e:
        return f"[error] {e}"

@limb("list_files", "List received and saved files. Filter by type (image/video/file/voice/gif) or list all.",
      {"type": {"type": "string", "description": "File type filter (image/video/file/voice/gif), empty for all"},
       "limit": {"type": "integer", "description": "Number of results (default 20)"}})
def tool_list_files(args, ctx):
    index_path = os.path.join(ctx["workspace"], "files", "index.json")
    if not os.path.exists(index_path):
        return "No files received yet."
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        return "File index read failed."

    file_type = args.get("type", "")
    if file_type:
        index = [e for e in index if e.get("type") == file_type]

    limit = args.get("limit", 20)
    recent = index[-limit:]
    recent.reverse()

    if not recent:
        return f"No files of type '{file_type}' found." if file_type else "No files received yet."

    lines = [f"Total {len(index)} files" + (f" (type: {file_type})" if file_type else "") + f", showing {len(recent)} most recent:"]
    for e in recent:
        size_kb = e.get("size", 0) / 1024
        size_str = f"{size_kb/1024:.1f}MB" if size_kb > 1024 else f"{size_kb:.0f}KB"
        lines.append(f"  - [{e.get('type', '?')}] {e.get('filename', '?')} ({size_str}) {e.get('time', '?')}")
        lines.append(f"    Path: {e.get('path', '?')}")
    return "\n".join(lines)
