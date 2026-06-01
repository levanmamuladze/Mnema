"""
LLM generation backpressure.

A single bounded semaphore caps how many chat generations stream from Ollama at
the same time. Beyond the cap, callers await a slot rather than piling more
concurrent decode work onto one GPU — which would slow *every* in-flight answer.
Holding the slot for the whole stream is intentional: the limit is on
*concurrent generations*, not request arrival rate (that's the rate limiter).

The semaphore is created lazily and memoised so the value is read from settings
at first use (after test env overrides are in place), and the gauge mirrors how
many slots are currently held for the /metrics endpoint.
"""
from __future__ import annotations

import asyncio
import weakref
from contextlib import asynccontextmanager

from core import metrics
from core.config import settings

# One semaphore per event loop. asyncio.Semaphore binds to the loop it first
# blocks on, so a single module-global would break the moment a second loop
# uses it (notably the per-test loops). Production runs one loop for the app's
# lifetime, so this is just a single entry there. WeakKeyDictionary lets closed
# loops (and their semaphores) get garbage-collected.
_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)

GAUGE_ACTIVE = "mnema_llm_generations_active"
GAUGE_WAITING = "mnema_llm_generations_waiting"


def get_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(max(1, settings.MAX_CONCURRENT_GENERATIONS))
        _semaphores[loop] = sem
    return sem


@asynccontextmanager
async def generation_slot():
    """Acquire one generation slot for the duration of a stream.

    Usage:
        async with generation_slot():
            async for token in pipeline.stream(...):
                ...
    """
    sem = get_semaphore()
    acquired = sem.locked()  # if already at capacity, this caller will wait
    if acquired:
        metrics.inc_gauge(GAUGE_WAITING)
    try:
        await sem.acquire()
    finally:
        if acquired:
            metrics.dec_gauge(GAUGE_WAITING)
    metrics.inc_gauge(GAUGE_ACTIVE)
    try:
        yield
    finally:
        sem.release()
        metrics.dec_gauge(GAUGE_ACTIVE)


def _reset_for_tests() -> None:
    _semaphores.clear()
