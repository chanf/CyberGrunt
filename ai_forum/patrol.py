"""Patrol forum actionable queue for a specific actor."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

DEFAULT_API_BASE = "http://localhost:8090"
DEFAULT_LOG_FILE = "test_reports/patrol_log.txt"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Poll /api/actionable and print reminders. "
            "Default actor is developer_ai for dev idle patrol."
        )
    )
    parser.add_argument("--api-base", default=os.environ.get("FORUM_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--author", default=os.environ.get("FORUM_AUTHOR", "developer_ai"))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("FORUM_ACTIONABLE_LIMIT", "50")))
    parser.add_argument("--interval", type=int, default=int(os.environ.get("FORUM_PATROL_INTERVAL", "60")))
    parser.add_argument("--timeout-sec", type=float, default=10.0)
    parser.add_argument("--log-file", default=os.environ.get("FORUM_PATROL_LOG", DEFAULT_LOG_FILE))
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one check and exit. Exit code 2 means actionable threads exist.",
    )
    parser.add_argument(
        "--show-empty",
        action="store_true",
        help="Print heartbeat line even when no actionable threads.",
    )
    parser.add_argument(
        "--show-unchanged",
        action="store_true",
        help="Print line even when actionable queue is unchanged.",
    )
    return parser.parse_args()


def _build_actionable_url(api_base: str, author: str, limit: int) -> str:
    base = api_base.rstrip("/")
    query = urllib.parse.urlencode({"author": author, "limit": max(1, min(limit, 200))})
    return f"{base}/api/actionable?{query}"


def _fetch_actionable(url: str, timeout_sec: float) -> List[Dict[str, Any]]:
    with urllib.request.urlopen(url, timeout=timeout_sec) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    threads = payload.get("threads", [])
    if not isinstance(threads, list):
        raise ValueError("invalid actionable payload")
    return threads


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _append_log(log_file: str, line: str) -> None:
    parent = os.path.dirname(log_file)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _summarize_threads(threads: List[Dict[str, Any]]) -> str:
    previews = []
    for thread in threads[:5]:
        previews.append(f"#{thread.get('id')} {thread.get('title', '').strip()}")
    suffix = " ..." if len(threads) > 5 else ""
    return "; ".join(previews) + suffix


def patrol() -> int:
    args = _parse_args()
    interval = max(5, int(args.interval))
    url = _build_actionable_url(args.api_base, args.author, args.limit)

    print(f"[{_stamp()}] patrol started: author={args.author}, interval={interval}s, url={url}")

    last_ids: List[int] = []
    saw_error = False

    while True:
        try:
            threads = _fetch_actionable(url, timeout_sec=args.timeout_sec)
            ids = [int(t.get("id")) for t in threads if isinstance(t.get("id"), int) or str(t.get("id")).isdigit()]

            if threads:
                changed = ids != last_ids
                if changed or args.show_unchanged:
                    summary = _summarize_threads(threads)
                    line = (
                        f"[{_stamp()}] actionable={len(threads)} for {args.author}: {summary}"
                    )
                    print(line)
                    _append_log(args.log_file, line)
            elif last_ids:
                line = f"[{_stamp()}] actionable queue cleared for {args.author}"
                print(line)
                _append_log(args.log_file, line)
            elif args.show_empty:
                print(f"[{_stamp()}] no actionable threads for {args.author}")

            last_ids = ids
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            saw_error = True
            line = f"[{_stamp()}] patrol error for {args.author}: {exc}"
            print(line)
            _append_log(args.log_file, line)

        if args.once:
            if saw_error:
                return 1
            return 2 if last_ids else 0

        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(patrol())
