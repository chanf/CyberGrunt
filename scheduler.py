"""
Built-in Scheduler - One-shot delayed tasks + Cron recurring tasks

jobs.json persisted, background thread checks every 10s.
On trigger: calls chat_fn(message, "scheduler") -> LLM processes -> sends message via tools.

Dependencies: stdlib + croniter (pip install croniter)
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta

log = logging.getLogger("agent")
CST = timezone(timedelta(hours=8))

# ============================================================
#  State
# ============================================================

_jobs_lock = threading.Lock()
_jobs = []
_jobs_file = ""
_chat_fn = None  # Injected by init()


def init(jobs_file, chat_fn):
    """Initialize scheduler. chat_fn signature: chat_fn(message: str, session_key: str) -> str"""
    global _jobs_file, _chat_fn
    _jobs_file = jobs_file
    _chat_fn = chat_fn
    _load_jobs()
    log.info(f"[scheduler] loaded {len(_jobs)} jobs")


def start():
    """Start background check thread"""
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


# ============================================================
#  CRUD Operations
# ============================================================

def add(args):
    """Create scheduled task. args: name, message, delay_seconds?, cron_expr?, once?"""
    name = args["name"]
    message = args["message"]
    delay = args.get("delay_seconds")
    cron_expr = args.get("cron_expr")
    once = args.get("once", True)

    now_str = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST")
    job = {"name": name, "message": message, "created": now_str, "created_ts": time.time()}

    if delay:
        trigger_at = time.time() + delay
        job["trigger_at"] = trigger_at
        job["type"] = "once"
        trigger_time = datetime.fromtimestamp(trigger_at, CST).strftime("%H:%M:%S")
        desc = f"One-shot task, triggers in {delay}s ({trigger_time})"
    elif cron_expr:
        job["cron_expr"] = cron_expr
        job["type"] = "once_cron" if once else "cron"
        desc = f"{'One-shot' if once else 'Recurring'} scheduled task, cron: {cron_expr}"
    else:
        return "[error] need delay_seconds or cron_expr"

    with _jobs_lock:
        _jobs[:] = [j for j in _jobs if j["name"] != name]
        _jobs.append(job)
        _save_jobs()

    log.info(f"[scheduler] added: {name} - {desc}")
    return f"Created scheduled task '{name}' - {desc}"


def list_all():
    with _jobs_lock:
        if not _jobs:
            return "No scheduled tasks"
        lines = []
        for j in _jobs:
            if j.get("type") == "once":
                remaining = max(0, int(j["trigger_at"] - time.time()))
                lines.append(f"- {j['name']} (one-shot, {remaining}s remaining): {j['message'][:50]}")
            else:
                lines.append(f"- {j['name']} ({j.get('cron_expr', '?')}): {j['message'][:50]}")
        return "\n".join(lines)


def remove(name):
    with _jobs_lock:
        before = len(_jobs)
        _jobs[:] = [j for j in _jobs if j["name"] != name]
        _save_jobs()
        if len(_jobs) < before:
            return f"Deleted scheduled task '{name}'"
        return f"Task '{name}' not found"


# ============================================================
#  Internals
# ============================================================

def _load_jobs():
    global _jobs
    if os.path.exists(_jobs_file):
        try:
            with open(_jobs_file, "r", encoding="utf-8") as f:
                _jobs = json.load(f)
        except Exception:
            _jobs = []
    else:
        _jobs = []


def _save_jobs():
    # Atomic write: write to temp file then rename, prevents corruption on crash
    tmp = _jobs_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_jobs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _jobs_file)


def _check():
    """Check and trigger due tasks"""
    now = time.time()
    to_trigger = []

    with _jobs_lock:
        remaining = []
        for job in _jobs:
            if job.get("type") == "once" and now >= job.get("trigger_at", 0):
                to_trigger.append(job)
            elif job.get("type") in ("cron", "once_cron"):
                try:
                    from croniter import croniter
                    # Use local timezone datetime for croniter to avoid UTC cron parsing
                    last_run = job.get("last_run") or job.get("created_ts", now - 60)
                    if isinstance(last_run, str):
                        last_run = now - 60
                    last_run_dt = datetime.fromtimestamp(last_run, CST)
                    cron = croniter(job["cron_expr"], last_run_dt)
                    next_dt = cron.get_next(datetime)
                    next_time = next_dt.timestamp()
                    if now >= next_time:
                        to_trigger.append(job)
                        if job["type"] == "cron":
                            job["last_run"] = now
                            remaining.append(job)
                        continue
                except Exception as e:
                    log.error(f"[scheduler] cron error for {job['name']}: {e}")
                remaining.append(job)
            else:
                remaining.append(job)
        _jobs[:] = remaining
        if to_trigger:
            _save_jobs()

    for job in to_trigger:
        log.info(f"[scheduler] triggering: {job['name']}")
        threading.Thread(target=_trigger, args=(job,), daemon=True).start()


def _trigger(job):
    """Trigger task, notify owner on failure"""
    try:
        reply = _chat_fn(job["message"], "scheduler")
        log.info(f"[scheduler] {job['name']} OK: {reply[:100] if reply else '(empty)'}")
    except Exception as e:
        log.error(f"[scheduler] {job['name']} FAILED: {e}", exc_info=True)
        # Notify owner that task failed
        try:
            _chat_fn(
                f"Scheduled task '{job['name']}' failed with error: {e}. Please notify the owner via message tool.",
                "scheduler"
            )
        except Exception:
            pass  # Notification also failed, can only wait for next heartbeat


def _log_heartbeat():
    """Print heartbeat log: task count + next trigger time for each cron task"""
    with _jobs_lock:
        if not _jobs:
            return
        lines = []
        for job in _jobs:
            if job.get("cron_expr"):
                try:
                    from croniter import croniter
                    lr = job.get("last_run") or job.get("created_ts", time.time() - 60)
                    lr_dt = datetime.fromtimestamp(lr, CST)
                    c = croniter(job["cron_expr"], lr_dt)
                    nxt = c.get_next(datetime)
                    lines.append(f"{job['name']}->{nxt.strftime('%H:%M')}")
                except Exception:
                    lines.append(f"{job['name']}->?")
        log.info(f"[scheduler] heartbeat: {len(_jobs)} jobs, next: {', '.join(lines)}")


def _loop():
    check_count = 0
    while True:
        try:
            _check()
        except Exception as e:
            log.error(f"[scheduler] loop error: {e}", exc_info=True)
        check_count += 1
        if check_count % 180 == 0:  # every 180 * 10s = 30 minutes
            _log_heartbeat()
        time.sleep(10)
