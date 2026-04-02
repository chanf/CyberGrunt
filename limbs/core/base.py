"""
Core Limbs - Essential system and file operations for CyberGrunt 2.0
"""

import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List

import messaging
import scheduler
from limbs.hub import limb

log = logging.getLogger("agent")

def _resolve_path(path: str, workspace: str) -> str:
    """
    Securely resolve path and ensure it stays within the workspace.
    Returns absolute path or raises PermissionError.
    """
    abs_workspace = os.path.abspath(workspace)
    # Join and resolve symlinks/relative parts
    target_path = os.path.abspath(os.path.join(abs_workspace, path))
    
    if not target_path.startswith(abs_workspace):
        raise PermissionError(f"Access denied: path '{path}' is outside workspace")
    return target_path

def _split_message(text: str, max_bytes: int = 1800) -> List[str]:
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
def tool_exec(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    raw_timeout = args.get("timeout", 60)
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError):
        timeout = 60
    timeout = max(1, min(timeout, 300))
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
def tool_message(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
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
def tool_read_file(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    try:
        fpath = _resolve_path(args["path"], ctx["workspace"])
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > 10000:
            content = content[:10000] + f"\n... (truncated, total {len(content)} chars)"
        return content or "(empty file)"
    except PermissionError as e:
        return f"[error] {e}"
    except FileNotFoundError:
        return f"[error] file not found: {args['path']}"
    except Exception as e:
        return f"[error] {e}"

@limb("write_file", "Write file (overwrite). Path relative to workspace directory.",
      {"path": {"type": "string", "description": "File path"},
       "content": {"type": "string", "description": "File content"}},
      ["path", "content"])
def tool_write_file(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    try:
        fpath = _resolve_path(args["path"], ctx["workspace"])
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(args["content"])
        return f"Written to {args['path']} ({len(args['content'])} chars)"
    except PermissionError as e:
        return f"[error] {e}"
    except Exception as e:
        return f"[error] {e}"

@limb("edit_file", "Edit file: replace old text with new text. "
      "For appending, use the end of file as old and old+new content as new.",
      {"path": {"type": "string", "description": "File path"},
       "old": {"type": "string", "description": "Original text to replace"},
       "new": {"type": "string", "description": "Replacement text"}},
      ["path", "old", "new"])
def tool_edit_file(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    try:
        fpath = _resolve_path(args["path"], ctx["workspace"])
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        if args["old"] not in content:
            return f"[error] old string not found in {args['path']}"
        content = content.replace(args["old"], args["new"], 1)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Edited {args['path']}"
    except PermissionError as e:
        return f"[error] {e}"
    except FileNotFoundError:
        return f"[error] file not found: {args['path']}"
    except Exception as e:
        return f"[error] {e}"

@limb("list_files", "List received and saved files. Filter by type (image/video/file/voice/gif) or list all.",
      {"type": {"type": "string", "description": "File type filter (image/video/file/voice/gif), empty for all"},
       "limit": {"type": "integer", "description": "Number of results (default 20)"}})
def tool_list_files(args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
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

    try:
        limit = int(args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, limit)
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
