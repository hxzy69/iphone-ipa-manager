"""Tiny pub/sub dispatcher - the extension point for future features.

The detection code never calls feature code directly. Instead it emits
``connect`` / ``disconnect`` events, and any feature can subscribe:

    dispatcher = EventDispatcher()
    dispatcher.on("connect", log_connect)          # basic logging (now)
    dispatcher.on("connect", run_ocr_pipeline)     # OCR automation (later)

A failing handler is logged but never kills the watcher loop.
"""

import logging
from typing import Callable, Dict, List

LOG = logging.getLogger("iphone-detector.events")

KNOWN_EVENTS = ("connect", "disconnect")


class EventDispatcher:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[[dict], None]]] = {
            event: [] for event in KNOWN_EVENTS
        }

    def on(self, event: str, handler: Callable[[dict], None]) -> None:
        """Register a handler for 'connect' or 'disconnect'."""
        if event not in self._handlers:
            raise ValueError("Unknown event %r (known: %s)" % (event, ", ".join(KNOWN_EVENTS)))
        self._handlers[event].append(handler)

    def emit(self, event: str, payload: dict) -> None:
        """Call all handlers registered for the event."""
        for handler in self._handlers.get(event, ()):
            try:
                handler(payload)
            except Exception:
                LOG.exception(
                    "Event handler %r failed for event %s",
                    getattr(handler, "__name__", handler),
                    event,
                )
