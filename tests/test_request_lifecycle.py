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


def test_anyio_scope_cancel_keeps_worker_permit_until_callback_exit():
    from types import SimpleNamespace
    started,release,finished=threading.Event(),threading.Event(),threading.Event()
    def work():
        started.set()
        try:assert release.wait(3)
        finally:finished.set()
    async def exercise():
        limiter=anyio.CapacityLimiter(1);request=SimpleNamespace(state=SimpleNamespace())
        async def endpoint():
            with anyio.CancelScope() as scope:
                request.state.scope=scope
                await request_lifecycle.run_worker(request,work,limiter=limiter)
                raise AssertionError('cancelled endpoint resumed')
        task=asyncio.create_task(endpoint())
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait,1),1.5)
            request.state.scope.cancel()
            await asyncio.sleep(.05)
            assert limiter.borrowed_tokens==1 and not request.state.database_worker_done.is_set()
        finally:
            release.set()
            await asyncio.wait_for(task,2)
        assert finished.is_set() and limiter.borrowed_tokens==0
    asyncio.run(exercise())


def test_cancel_before_runner_dispatch_does_not_leave_unfinished_event(monkeypatch):
    from types import SimpleNamespace
    original=asyncio.create_task;calls=[]
    async def exercise():
        request=SimpleNamespace(state=SimpleNamespace());limiter=anyio.CapacityLimiter(1)
        def cancel_before_dispatch(coroutine):
            task=original(coroutine)
            asyncio.current_task().cancel()
            return task
        monkeypatch.setattr(request_lifecycle.asyncio,'create_task',cancel_before_dispatch)
        try:
            await request_lifecycle.run_worker(request,lambda:calls.append('ran'),limiter=limiter)
            raise AssertionError('cancelled request resumed')
        except asyncio.CancelledError:pass
        assert request.state.database_worker_done.is_set()
        assert not calls and limiter.borrowed_tokens==0
    asyncio.run(exercise())
