"""Post a development completion update to AI forum by API."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_API_BASE = "http://localhost:8090"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Post a completion reply so reviewer AI can schedule testing."
    )
    parser.add_argument("--thread-id", type=int, required=True, help="Forum thread id")
    parser.add_argument("--summary", required=True, help="One-line completion summary")
    parser.add_argument(
        "--author",
        default=os.environ.get("FORUM_AUTHOR", "developer_ai"),
        help="Author name, default developer_ai",
    )
    parser.add_argument(
        "--api-base",
        default=os.environ.get("FORUM_API_BASE", DEFAULT_API_BASE),
        help="Forum base URL",
    )
    parser.add_argument(
        "--test",
        action="append",
        default=[],
        help="Test evidence line. Can repeat --test multiple times.",
    )
    parser.add_argument(
        "--run-test-cmd",
        action="append",
        default=[],
        help=(
            "Run a real test command before posting. "
            "Can repeat --run-test-cmd multiple times."
        ),
    )
    parser.add_argument(
        "--test-timeout-sec",
        type=float,
        default=600.0,
        help="Timeout in seconds for each --run-test-cmd command.",
    )
    parser.add_argument(
        "--max-test-output-lines",
        type=int,
        default=3,
        help="How many trailing output lines to include in failed test summary.",
    )
    parser.add_argument(
        "--allow-no-tests",
        action="store_true",
        help="Allow posting without any test evidence (not recommended).",
    )
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed file path. Can repeat --changed-file multiple times.",
    )
    parser.add_argument(
        "--note",
        default="请 IronGate 安排测试并回复测试结果。",
        help="Call-to-action note for reviewer AI.",
    )
    parser.add_argument(
        "--details",
        default="",
        help="Extra details (optional).",
    )
    parser.add_argument(
        "--resolve",
        action="store_true",
        help="Also update thread status to resolved (not recommended before QA confirms).",
    )
    return parser.parse_args()


def _post_json(url: str, payload: Dict[str, Any], timeout_sec: float = 10.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _format_bullet(items: List[str]) -> str:
    if not items:
        return "- (无)"
    return "\n".join(f"- {item}" for item in items)


def _tail_lines(text: str, max_lines: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return " | ".join(lines[-max(1, max_lines):])


def _run_test_commands(
    commands: List[str],
    timeout_sec: float,
    max_output_lines: int,
) -> Tuple[List[str], List[str]]:
    evidence: List[str] = []
    failed: List[str] = []
    for raw in commands:
        cmd = raw.strip()
        if not cmd:
            continue
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                executable="/bin/zsh",
                text=True,
                capture_output=True,
                timeout=max(1.0, timeout_sec),
            )
        except subprocess.TimeoutExpired:
            line = f"`{cmd}` -> FAIL (timeout {timeout_sec:.0f}s)"
            evidence.append(line)
            failed.append(line)
            continue

        if proc.returncode == 0:
            evidence.append(f"`{cmd}` -> PASS")
            continue

        merged = "\n".join(x for x in [proc.stdout, proc.stderr] if x)
        tail = _tail_lines(merged, max_output_lines)
        if tail:
            fail_line = f"`{cmd}` -> FAIL (code {proc.returncode}): {tail}"
        else:
            fail_line = f"`{cmd}` -> FAIL (code {proc.returncode})"
        evidence.append(fail_line)
        failed.append(fail_line)
    return evidence, failed


def _build_reply_body(
    summary: str,
    changed_files: List[str],
    tests: List[str],
    details: str,
    note: str,
) -> str:
    chunks = [
        "[开发完成同步]",
        "",
        "功能摘要:",
        f"- {summary.strip()}",
        "",
        "代码变更:",
        _format_bullet([x.strip() for x in changed_files if x.strip()]),
        "",
        "自测证据:",
        _format_bullet([x.strip() for x in tests if x.strip()]),
    ]

    clean_details = details.strip()
    if clean_details:
        chunks.extend(["", "补充说明:", clean_details])

    clean_note = note.strip()
    if clean_note:
        chunks.extend(["", clean_note])

    return "\n".join(chunks).strip()


def _resolve_status_if_needed(args: argparse.Namespace, api_base: str, note: str) -> Optional[Dict[str, Any]]:
    if not args.resolve:
        return None
    status_payload = {
        "author": args.author,
        "status": "resolved",
        "note": note or "开发方标记已完成，待验证。",
    }
    status_url = f"{api_base}/api/threads/{args.thread_id}/status"
    return _post_json(status_url, status_payload)


def main() -> int:
    args = _parse_args()
    api_base = args.api_base.rstrip("/")
    auto_test_evidence, failed_cmds = _run_test_commands(
        commands=args.run_test_cmd,
        timeout_sec=args.test_timeout_sec,
        max_output_lines=max(1, int(args.max_test_output_lines)),
    )
    manual_test_evidence = [x.strip() for x in args.test if x and x.strip()]
    all_test_evidence = auto_test_evidence + manual_test_evidence

    if failed_cmds:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "test commands failed, reply blocked",
                    "failed": failed_cmds,
                },
                ensure_ascii=False,
            )
        )
        return 2

    if not all_test_evidence and not args.allow_no_tests:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        "no test evidence; provide --run-test-cmd/--test, "
                        "or use --allow-no-tests explicitly"
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 2

    reply_body = _build_reply_body(
        summary=args.summary,
        changed_files=args.changed_file,
        tests=all_test_evidence,
        details=args.details,
        note=args.note,
    )

    reply_url = f"{api_base}/api/threads/{args.thread_id}/replies"
    reply_payload = {
        "author": args.author,
        "body": reply_body,
    }
    reply_resp = _post_json(reply_url, reply_payload)

    status_resp = _resolve_status_if_needed(args, api_base, args.note)

    print(
        json.dumps(
            {
                "ok": True,
                "thread_id": args.thread_id,
                "reply_id": reply_resp.get("reply", {}).get("id"),
                "status_updated": bool(status_resp),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
