"""Keep request-owned resources alive until an offloaded worker has exited."""
import asyncio
import threading

import anyio


async def drain_worker(done: threading.Event | None) -> bool:
    """Wait through repeated native task cancellation; report interruption.

    ``asyncio.shield`` protects the waiter task, but a direct ``Task.cancel``
    still interrupts its caller. Keep awaiting that same waiter until the worker
    signals completion so the caller can safely roll back and close SQLite.
    """
    if done is None or done.is_set():
        return False
    interrupted = False
    try:
        await anyio.lowlevel.checkpoint()
    except asyncio.CancelledError:
        interrupted = True
    waiter = asyncio.create_task(asyncio.to_thread(done.wait))
    with anyio.CancelScope(shield=True):
        while not waiter.done():
            try:
                await asyncio.shield(waiter)
            except asyncio.CancelledError:
                interrupted = True
    waiter.result()
    return interrupted
