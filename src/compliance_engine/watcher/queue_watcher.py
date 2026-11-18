"""Thread-safe ticket batching watcher with debounce timers and worker queue."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from queue import Queue
from threading import Lock, Thread, Timer


class DebouncedTicketWatcher:
    """Batches multi-file uploads per ticket ID and dispatches FIFO after debounce settles."""

    def __init__(
        self,
        on_ticket_ready: Callable[[str, list[str]], None],
        delay_seconds: float = 60.0,
        max_workers: int = 1,
        ticket_pattern: str = r"^([A-Za-z0-9-]+)_",
    ) -> None:
        self.on_ticket_ready = on_ticket_ready
        self.delay_seconds = delay_seconds
        self.ticket_regex = re.compile(ticket_pattern)

        self._lock = Lock()
        self.ticket_files: dict[str, list[str]] = {}
        self.ticket_timers: dict[str, Timer] = {}
        self.ticket_state: dict[str, str] = {}
        self._queue: Queue[str | None] = Queue()
        self._stopped = False

        self._workers = [
            Thread(target=self._worker_loop, name=f"ticket-worker-{i}", daemon=True)
            for i in range(max_workers)
        ]
        for worker in self._workers:
            worker.start()

    def parse_ticket(self, path_or_name: str) -> str | None:
        name = os.path.basename(path_or_name)
        match = self.ticket_regex.match(name)
        return match.group(1) if match else None

    def handle_file_arrival(self, file_ref: str) -> str | None:
        """Register an incoming file for its ticket and reset the debounce timer."""
        ticket = self.parse_ticket(file_ref)
        if not ticket:
            logging.warning("Unable to infer ticket prefix from '%s'; skipping.", file_ref)
            return None

        with self._lock:
            self.ticket_files.setdefault(ticket, []).append(file_ref)
            self.ticket_state.setdefault(ticket, "idle")
            existing_timer = self.ticket_timers.get(ticket)
            if existing_timer is not None:
                existing_timer.cancel()

            timer = Timer(self.delay_seconds, self._enqueue_ticket, args=(ticket,))
            timer.daemon = True
            self.ticket_timers[ticket] = timer
            timer.start()
        return ticket

    def _enqueue_ticket(self, ticket: str) -> None:
        with self._lock:
            if self._stopped:
                return
            if self.ticket_state.get(ticket) == "queued":
                return
            files = list(self.ticket_files.get(ticket, []))
            self.ticket_files[ticket] = []
            self.ticket_state[ticket] = "queued"
            self._batched_payload = getattr(self, "_batched_payload", {})
            self._batched_payload[ticket] = files
            self._queue.put(ticket)

    def _worker_loop(self) -> None:
        while True:
            ticket = self._queue.get()
            if ticket is None:
                self._queue.task_done()
                break
            try:
                with self._lock:
                    self.ticket_state[ticket] = "processing"
                    payload_map = getattr(self, "_batched_payload", {})
                    files = payload_map.pop(ticket, [])
                self.on_ticket_ready(ticket, files)
            except Exception as exc:
                logging.error("Ticket worker failed for '%s': %s", ticket, exc, exc_info=True)
            finally:
                with self._lock:
                    self.ticket_state[ticket] = "idle"
                self._queue.task_done()

    def stop(self, timeout: float = 2.0) -> None:
        """Cancel pending debounce timers and gracefully stop background worker threads."""
        with self._lock:
            self._stopped = True
            for timer in self.ticket_timers.values():
                timer.cancel()
            self.ticket_timers.clear()
        for _ in self._workers:
            self._queue.put(None)
        for worker in self._workers:
            worker.join(timeout=timeout)
