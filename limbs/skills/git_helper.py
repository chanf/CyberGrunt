"""Git helper skill for Forge (Developer)."""

import subprocess
import os
import logging
from limbs.hub import limb

log = logging.getLogger("agent")


@limb(
    name="git_status",
    description="Get git status showing modified, added, deleted, and untracked files",
    properties={}
)
def git_status(args, ctx):
    """Get git status."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True
    )

    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    modified = [line[3:] for line in lines if line.startswith(" M") or line.startswith("M")]
    added = [line[3:] for line in lines if line.startswith("A ")]
    deleted = [line[3:] for line in lines if line.startswith(" D") or line.startswith("D")]
    untracked = [line[3:] for line in lines if line.startswith("??")]

    return {
        "modified": modified,
        "added": added,
        "deleted": deleted,
        "untracked": untracked,
        "has_changes": bool(lines),
        "summary": f"{len(modified)} modified, {len(added)} added, {len(deleted)} deleted, {len(untracked)} untracked"
    }


@limb(
    name="git_diff",
    description="Get git diff output showing changes",
    properties={
        "file_path": {
            "type": "string",
            "description": "Specific file to diff. If empty, diffs all files."
        }
    }
)
def git_diff(args, ctx):
    """Get git diff."""
    file_path = args.get("file_path", "")
    cmd = ["git", "diff"]
    if file_path:
        cmd.append(file_path)

    result = subprocess.run(cmd, capture_output=True, text=True)
    return {"diff": result.stdout, "file": file_path or "all"}


@limb(
    name="git_log",
    description="Get recent git commits",
    properties={
        "limit": {
            "type": "integer",
            "description": "Number of commits to show (default: 10)"
        }
    }
)
def git_log(args, ctx):
    """Get recent git log."""
    limit = args.get("limit", 10)
    result = subprocess.run(
        ["git", "log", f"-{limit}", "--pretty=format:%H|%an|%ad|%s", "--date=short"],
        capture_output=True,
        text=True
    )

    commits = []
    for line in result.stdout.strip().split("\n"):
        if line:
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash": parts[0][:8],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3]
                })

    return {"commits": commits, "count": len(commits)}


@limb(
    name="git_add",
    description="Stage files for commit",
    properties={
        "files": {
            "type": "string",
            "description": "Comma-separated list of file paths to stage (e.g., 'file1.py,file2.py')"
        }
    }
)
def git_add(args, ctx):
    """Stage files for commit."""
    files_str = args.get("files", "")
    if not files_str:
        return {"success": False, "error": "No files specified"}

    files = [f.strip() for f in files_str.split(",")]
    cmd = ["git", "add"] + files
    result = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "success": result.returncode == 0,
        "files": files,
        "output": result.stdout + result.stderr
    }


@limb(
    name="git_commit",
    description="Create a git commit with a message",
    properties={
        "message": {
            "type": "string",
            "description": "Commit message"
        }
    }
)
def git_commit(args, ctx):
    """Create a commit."""
    message = args.get("message", "")
    if not message:
        return {"success": False, "error": "Commit message is required"}

    result = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True,
        text=True
    )
    return {
        "success": result.returncode == 0,
        "message": message,
        "output": result.stdout + result.stderr
    }


log.info("[skills] git_helper loaded")
