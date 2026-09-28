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


async def run_worker(request, work, *, limiter=None):
    """Own both the thread's permit and request resources through cancellation.

    The endpoint may be cancelled directly, so its await cannot own the permit.
    A separate runner survives that cancellation. The lock arbitrates cancellation
    before dispatch against the callback starting; an abandoned callback never
    touches the request connection, even if already submitted to the thread pool.
    """
    done = threading.Event()
    request.state.database_worker_done = done
    lock = threading.Lock()
    phase = 'pending'

    def callback():
        nonlocal phase
        with lock:
            if phase == 'abandoned':
                return None
            phase = 'running'
        try:
            return work()
        finally:
            with lock:
                phase = 'finished'
                done.set()

    async def run():
        # Also survive cancellation inherited from the request's AnyIO scope.
        with anyio.CancelScope(shield=True):
            try:
                return await anyio.to_thread.run_sync(callback, limiter=limiter)
            finally:
                with lock:
                    if phase != 'running':
                        done.set()

    runner = asyncio.create_task(run())
    try:
        return await asyncio.shield(runner)
    except asyncio.CancelledError:
        with lock:
            if phase == 'pending':
                phase = 'abandoned'
                # Covers cancellation before the runner's coroutine even starts.
                done.set()
                runner.cancel()
        with anyio.CancelScope(shield=True):
            while not runner.done():
                try:
                    await asyncio.shield(runner)
                except asyncio.CancelledError:
                    pass
                except BaseException:
                    break
        # Consume errors without replacing cancellation; middleware rolls back.
        if not runner.cancelled():
            runner.exception()
        raise
