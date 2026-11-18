"""Event-driven ticket grouping watcher with debounce timers and worker queue."""

from compliance_engine.watcher.queue_watcher import DebouncedTicketWatcher

__all__ = ["DebouncedTicketWatcher"]
