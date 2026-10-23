"""Share one live portal fetch between hubs that poll the same search URL.

In the worker every user has a child Hub, and many users watch the same portal search (for example
all of realitymix.cz/reality/byty/pronajem). Without sharing, each hub issues its own request, so a
minute of monitoring turns into ~100 identical polls that queue at the per-portal limiter and time out.

A poll that is already in flight is joined, and a successful result is reused for a few seconds.
Every caller receives its own deep copy, so downstream code may mutate listings freely.
"""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any, Awaitable, Callable, Hashable

_MAX_ENTRIES = 512


class LiveShare:
    def __init__(self) -> None:
        self._inflight: dict[Hashable, asyncio.Task[Any]] = {}
        self._done: dict[Hashable, tuple[float, Any]] = {}
        self.joined = 0
        self.reused = 0
        self.fetched = 0

    def _prune(self, now: float, ttl: float) -> None:
        if len(self._done) <= _MAX_ENTRIES:
            return
        for key in [k for k, (at, _) in self._done.items() if now - at > ttl]:
            self._done.pop(key, None)
        while len(self._done) > _MAX_ENTRIES:
            self._done.pop(next(iter(self._done)))

    async def get(
        self,
        key: Hashable,
        ttl: float,
        factory: Callable[[], Awaitable[Any]],
        *,
        cacheable: Callable[[Any], bool] = lambda _result: True,
    ) -> Any:
        """Return factory()'s result for `key`, sharing it with concurrent and recent callers."""
        if ttl <= 0:
            return await factory()
        now = time.monotonic()
        cached = self._done.get(key)
        if cached is not None and now - cached[0] <= ttl:
            self.reused += 1
            return copy.deepcopy(cached[1])
        task = self._inflight.get(key)
        if task is not None and not task.done():
            self.joined += 1
        else:
            task = asyncio.ensure_future(factory())
            self._inflight[key] = task
            self.fetched += 1

            def _finish(done: asyncio.Task[Any], key: Hashable = key) -> None:
                if self._inflight.get(key) is done:
                    self._inflight.pop(key, None)
                if done.cancelled() or done.exception() is not None:
                    return
                result = done.result()
                if cacheable(result):
                    stamp = time.monotonic()
                    self._done[key] = (stamp, result)
                    self._prune(stamp, ttl)

            task.add_done_callback(_finish)
        # shield: one waiter timing out must not cancel the fetch the others are waiting for.
        result = await asyncio.shield(task)
        return copy.deepcopy(result)


shared_live = LiveShare()
