"""Forum runtime: event bus + worker loops."""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Dict, Optional

from .forum_store import ForumStore

log = logging.getLogger("ai_forum")


class SSEEventBus:
    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                return

    def publish(self, event_type: str, content: Dict[str, Any]) -> None:
        packet = {
            "type": event_type,
            "content": content,
            "ts": time.time(),
        }
        with self._lock:
            targets = list(self._subs)
        for q in targets:
            q.put(packet)


class ForumRuntime:
    def __init__(
        self,
        store: ForumStore,
        llm_client: Any,
        settings: Dict[str, Any],
        event_bus: Optional[SSEEventBus] = None,
    ):
        self.store = store
        self.llm = llm_client
        self.settings = settings
        self.event_bus = event_bus or SSEEventBus()

        self._stop = threading.Event()
        self._threads = []

    def start_workers(self) -> None:
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._poster_loop, daemon=True, name="forum-poster"),
            threading.Thread(target=self._reviewer_loop, daemon=True, name="forum-reviewer"),
        ]
        for t in self._threads:
            t.start()
        log.info("Forum workers started.")

    def stop_workers(self, timeout: float = 2.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads = []
        log.info("Forum workers stopped.")

    def poster_tick(self) -> None:
        open_count = self.store.count_open_threads()
        max_open = int(self.settings.get("max_open_threads", 20))
        if open_count >= max_open:
            log.info("Skip poster tick: open threads %d >= max %d", open_count, max_open)
            return

        try:
            post = self.llm.generate_post(open_count)
            thread = self.store.create_thread(
                title=post["title"],
                body=post["body"],
                author="developer_ai",
            )
            self.event_bus.publish("thread_created", {
                "thread": _thread_summary(thread),
            })
        except Exception as exc:
            log.error("Poster tick failed: %s", exc, exc_info=True)

    def reviewer_tick(self) -> None:
        open_thread = self.store.get_oldest_open_thread()
        if not open_thread:
            return

        try:
            reply_text = self.llm.generate_reply(open_thread)
            reply = self.store.create_reply(
                thread_id=int(open_thread["id"]),
                body=reply_text,
                author="reviewer_ai",
            )
            updated = self.store.get_thread(int(open_thread["id"]))
            self.event_bus.publish(
                "thread_replied",
                {
                    "thread": _thread_summary(updated),
                    "reply": reply,
                },
            )
        except Exception as exc:
            log.error("Reviewer tick failed: %s", exc, exc_info=True)

    def _poster_loop(self) -> None:
        interval = max(1, int(self.settings.get("poster_interval_sec", 120)))
        while not self._stop.is_set():
            self.poster_tick()
            self._stop.wait(interval)

    def _reviewer_loop(self) -> None:
        interval = max(1, int(self.settings.get("review_interval_sec", 30)))
        while not self._stop.is_set():
            self.reviewer_tick()
            self._stop.wait(interval)


def _thread_summary(thread: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not thread:
        return None
    return {
        "id": int(thread["id"]),
        "title": thread["title"],
        "author": thread["author"],
        "status": thread["status"],
        "created_at": thread["created_at"],
        "updated_at": thread["updated_at"],
        "reply_count": len(thread.get("replies", [])),
    }
