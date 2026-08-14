"""Live events from a run in progress, for anything watching.

A run takes 30 seconds to two minutes with models in the path, and until now
the page sent a brief and showed a spinner for all of it. Everything
interesting -- a credential minted, a card fetched, a leg answering, a draft
sent back for a rewrite -- was already happening and was visible only in the
service log, which is no use to someone watching.

**A ring buffer, not a queue.** A subscriber that stops reading must not grow
memory without bound and must not block the run, so each subscriber gets a
bounded buffer and loses its *oldest* events when it overflows. Losing old
events is the right failure: the newest ones are the ones being watched, and a
run that stalls because a browser tab was closed would be the worst possible
trade for a debug view.

**Emitting must never fail a run.** Every publish is guarded. The mesh's job is
to answer a brief; telling a page about it is strictly secondary, and an
observer that can take a mesh down is not an observer.
"""

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime

log = logging.getLogger("events")

#: Per-subscriber. About a minute of a busy run, and small enough that a
#: forgotten tab costs nothing.
SUBSCRIBER_BUFFER = 500

#: Kept for a subscriber that arrives mid-run, so a page opened late still
#: shows what has happened rather than an empty log.
REPLAY_BUFFER = 200


class EventBus:
    """Fan events out to any number of subscribers, losing none of a run's."""

    def __init__(self, replay: int = REPLAY_BUFFER) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._replay: deque[dict] = deque(maxlen=replay)

    def publish(self, kind: str, text: str, **fields) -> None:
        """Record one event and hand it to every subscriber.

        Synchronous and non-blocking on purpose: this is called from the middle
        of a fan-out, and an `await` here would put a subscriber's back-pressure
        onto the mesh.
        """
        event = {
            "t": datetime.now(UTC).isoformat(),
            "kind": kind,
            "text": text,
            **fields,
        }
        self._replay.append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest and take the newest. A slow reader loses
                # history, never the present.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)
            except Exception as exc:  # noqa: BLE001 - an observer cannot fail a run
                log.debug("dropping an event for a broken subscriber: %s", exc)

    def replay(self) -> list[dict]:
        return list(self._replay)

    async def subscribe(self) -> AsyncIterator[dict]:
        """Every event from now, after replaying what was already recorded."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_BUFFER)
        self._subscribers.add(queue)
        try:
            for event in self.replay():
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


#: The process-wide bus. One master, one bus; a second one would mean a page
#: subscribed to events no run publishes to.
BUS = EventBus()


def emit(kind: str, text: str, **fields) -> None:
    """Publish to the process bus, and never raise doing it."""
    try:
        BUS.publish(kind, text, **fields)
    except Exception as exc:  # noqa: BLE001 - telling a page must not fail a run
        log.debug("event dropped: %s", exc)


__all__ = ["BUS", "EventBus", "emit"]
