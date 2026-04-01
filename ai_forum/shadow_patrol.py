"""Shadow's forum patrol - monitor all threads for anomalies."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

DEFAULT_API_BASE = "http://localhost:8090"
DEFAULT_LOG_FILE = "test_reports/shadow_patrol_log.txt"
STALE_THRESHOLD_HOURS = 24  # 帖子超过24小时无回复视为陈旧


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Shadow's forum patrol: monitor all threads for anomalies."
    )
    parser.add_argument("--api-base", default=os.environ.get("FORUM_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--interval", type=int, default=int(os.environ.get("SHADOW_PATROL_INTERVAL", "60")))
    parser.add_argument("--timeout-sec", type=float, default=10.0)
    parser.add_argument("--log-file", default=os.environ.get("SHADOW_PATROL_LOG", DEFAULT_LOG_FILE))
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one check and exit.",
    )
    parser.add_argument(
        "--show-empty",
        action="store_true",
        help="Print heartbeat line even when no issues found.",
    )
    return parser.parse_args()


def _build_threads_url(api_base: str, limit: int) -> str:
    base = api_base.rstrip("/")
    query = urllib.parse.urlencode({"status": "all", "limit": max(1, min(limit, 200))})
    return f"{base}/api/threads?{query}"


def _fetch_threads(url: str, timeout_sec: float) -> List[Dict[str, Any]]:
    with urllib.request.urlopen(url, timeout=timeout_sec) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    threads = payload.get("threads", [])
    if not isinstance(threads, list):
        raise ValueError("invalid threads payload")
    return threads


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _append_log(log_file: str, line: str) -> None:
    parent = os.path.dirname(log_file)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _parse_iso_time(iso_str: str) -> datetime:
    """Parse ISO 8601 string to datetime."""
    try:
        # Remove microseconds if present
        if "." in iso_str:
            iso_str = iso_str.split(".")[0] + "Z" if iso_str.endswith("+") else iso_str.rsplit("+", 1)[0]
        # Handle timezone
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        return datetime.fromisoformat(iso_str)
    except (ValueError, IndexError):
        return datetime.now(timezone.utc)


def _check_thread_staleness(thread: Dict[str, Any], threshold_hours: int) -> Dict[str, Any]:
    """Check if a thread is stale (no recent activity)."""
    updated_str = thread.get("updated_at", "")
    if not updated_str:
        return {"stale": False, "reason": "no timestamp"}

    updated = _parse_iso_time(updated_str)
    now = datetime.now(timezone.utc)
    age_hours = (now - updated).total_seconds() / 3600

    if age_hours > threshold_hours:
        return {
            "stale": True,
            "age_hours": round(age_hours, 1),
            "last_activity": updated_str,
        }
    return {"stale": False, "age_hours": round(age_hours, 1)}


def _detect_anomalies(threads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect forum anomalies requiring Shadow's attention."""
    anomalies = []

    # 1. 检查陈旧的 pending 帖子
    for thread in threads:
        if thread.get("status") != "pending":
            continue

        staleness = _check_thread_staleness(thread, STALE_THRESHOLD_HOURS)
        if staleness["stale"]:
            anomalies.append({
                "type": "stale_pending",
                "thread_id": thread.get("id"),
                "title": thread.get("title"),
                "age_hours": staleness["age_hours"],
                "last_actor": thread.get("last_actor"),
                "severity": "warning" if staleness["age_hours"] < 48 else "critical",
            })

    # 2. 检查是否有多个 pending 帖子同时等待同一个人
    pending_by_actor: Dict[str, List[Dict]] = {}
    for thread in threads:
        if thread.get("status") != "pending":
            continue
        actor = thread.get("last_actor") or thread.get("author")
        if actor:
            pending_by_actor.setdefault(actor, []).append(thread)

    for actor, actor_threads in pending_by_actor.items():
        if len(actor_threads) >= 3:  # 同一个人有3个或更多待办
            anomalies.append({
                "type": "bottleneck",
                "actor": actor,
                "count": len(actor_threads),
                "thread_ids": [t.get("id") for t in actor_threads],
                "severity": "info",
            })

    return anomalies


def _format_anomaly(anomaly: Dict[str, Any]) -> str:
    """Format anomaly for logging."""
    if anomaly["type"] == "stale_pending":
        severity_icon = "⚠️" if anomaly["severity"] == "warning" else "🚨"
        return (
            f"{severity_icon} [陈旧] #{anomaly['thread_id']} {anomaly['title']} - "
            f"已{anomaly['age_hours']}小时无更新，等待 {anomaly.get('last_actor', 'N/A')}"
        )
    elif anomaly["type"] == "bottleneck":
        return (
            f"📊 [瓶颈] {anomaly['actor']} 有 {anomaly['count']} 个待办堆积 "
            f"(#{', #'.join(map(str, anomaly['thread_ids']))})"
        )
    return str(anomaly)


def patrol() -> int:
    args = _parse_args()
    interval = max(5, int(args.interval))
    url = _build_threads_url(args.api_base, limit=100)

    print(f"[{_stamp()}] Shadow patrol started: interval={interval}s")

    last_anomaly_count = 0

    while True:
        try:
            threads = _fetch_threads(url, timeout_sec=args.timeout_sec)
            anomalies = _detect_anomalies(threads)

            pending_count = sum(1 for t in threads if t.get("status") == "pending")
            resolved_count = sum(1 for t in threads if t.get("status") == "resolved")

            if anomalies:
                for anomaly in anomalies:
                    line = f"[{_stamp()}] {_format_anomaly(anomaly)}"
                    print(line)
                    _append_log(args.log_file, line)
                last_anomaly_count = len(anomalies)
            elif last_anomaly_count > 0:
                line = f"[{_stamp()}] ✓ 所有异常已清除"
                print(line)
                _append_log(args.log_file, line)
                last_anomaly_count = 0
            elif args.show_empty:
                print(f"[{_stamp()}] 论坛正常 - pending:{pending_count} resolved:{resolved_count}")

        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            line = f"[{_stamp()}] patrol error: {exc}"
            print(line)
            _append_log(args.log_file, line)

        if args.once:
            return 1 if anomalies else 0

        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(patrol())
