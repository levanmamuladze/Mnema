"""Tests for the LLM generation backpressure semaphore."""
from __future__ import annotations

import asyncio

import pytest

from core import concurrency, metrics
from core.config import settings


@pytest.fixture
def limit_two():
    """Pin the concurrency limit to 2 and reset the memoised semaphore."""
    old = settings.MAX_CONCURRENT_GENERATIONS
    settings.MAX_CONCURRENT_GENERATIONS = 2
    concurrency._reset_for_tests()
    metrics.reset()
    yield
    settings.MAX_CONCURRENT_GENERATIONS = old
    concurrency._reset_for_tests()
    metrics.reset()


def _active() -> int:
    return metrics._gauges.get(concurrency.GAUGE_ACTIVE, 0)


async def test_semaphore_uses_configured_limit(limit_two):
    sem = concurrency.get_semaphore()
    assert sem._value == 2


async def test_caps_concurrent_generations(limit_two):
    started: list[int] = []
    release = asyncio.Event()

    async def worker(i: int) -> None:
        async with concurrency.generation_slot():
            started.append(i)
            await release.wait()

    tasks = [asyncio.create_task(worker(i)) for i in range(3)]
    await asyncio.sleep(0.05)  # let the loop schedule acquisitions

    # Only two slots exist, so only two workers entered the block.
    assert len(started) == 2
    assert _active() == 2

    # Releasing lets the third proceed; all complete and the gauge unwinds.
    release.set()
    await asyncio.gather(*tasks)
    assert len(started) == 3
    assert _active() == 0


async def test_slot_releases_on_exception(limit_two):
    with pytest.raises(ValueError):
        async with concurrency.generation_slot():
            assert _active() == 1
            raise ValueError("boom")
    # The slot must be returned even when the body raises.
    assert _active() == 0
    assert concurrency.get_semaphore()._value == 2
