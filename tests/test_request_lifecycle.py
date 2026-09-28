"""A cancelled request drains its worker without spinning the event loop."""
import asyncio
import threading

import anyio

from catalog import request_lifecycle


def test_anyio_cancelled_scope_drains_with_bounded_waits(monkeypatch):
    done = threading.Event()
    waits = []
    original_shield = asyncio.shield

    def counted_shield(waiter):
        waits.append(1)
        return original_shield(waiter)

    monkeypatch.setattr(request_lifecycle.asyncio, 'shield', counted_shield)

    async def exercise():
        asyncio.get_running_loop().call_later(0.03, done.set)
        with anyio.CancelScope() as scope:
            scope.cancel()
            interrupted = await request_lifecycle.drain_worker(done)
        return interrupted

    assert asyncio.run(exercise()) is True
    assert done.is_set()
    assert len(waits) <= 3
