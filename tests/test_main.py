import asyncio
import os
import time
import threading
from signal import SIGINT, SIGTERM
from concurrent.futures import ThreadPoolExecutor
from aiorun import run, shutdown_waits_for, _DO_NOT_CANCEL_COROS
import pytest


def kill(sig=SIGTERM, after=0.01):
    """Tool to send a signal after a pause"""
    def job():
        pid = os.getpid()
        time.sleep(after)
        os.kill(pid, sig)

    t = threading.Thread(target=job)
    t.start()


def newloop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def test_sigterm():
    """Basic SIGTERM"""
    async def main():
        await asyncio.sleep(5.0)

    kill(SIGTERM)
    loop = newloop()
    run(main(), loop=loop)
    assert not loop.is_closed()


def test_uvloop():
    """Basic SIGTERM"""
    async def main():
        await asyncio.sleep(0)
        asyncio.get_event_loop().stop()

    run(main(), use_uvloop=True)


def test_no_coroutine():
    """Signal should still work without a main coroutine"""
    kill(SIGTERM)
    newloop()
    run()


def test_sigint():
    """Basic SIGINT"""
    kill(SIGINT)
    newloop()
    run()


def test_exe():
    """Custom executor"""
    exe = ThreadPoolExecutor()
    kill(SIGTERM)
    newloop()
    run(executor=exe)


def test_sigint_pause():
    """Trying to send multiple signals."""
    items = []

    async def main():
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            await asyncio.sleep(0.04)
            items.append(True)

    # The first sigint triggers shutdown mode, so all tasks are cancelled.
    # Note that main() catches CancelledError, and does a little bit more
    # work before exiting.
    kill(SIGINT, after=0.02)
    # The second sigint should have no effect, because aiorun signal
    # handler disables itself after the first sigint received, above.
    kill(SIGINT, after=0.03)
    newloop()
    run(main())
    assert items  # Verify that main() ran till completion.


def test_sigterm_enduring_create_task():
    """Calling `shutdown_waits_for()` via `create_task()`"""

    items = []

    async def corofn():
        await asyncio.sleep(0.02)
        items.append(True)

    async def main():
        loop = asyncio.get_event_loop()
        loop.create_task(shutdown_waits_for(corofn()))

    kill(SIGTERM, after=0.01)
    newloop()
    run(main())
    assert items


def test_sigterm_enduring_ensure_future():
    """Calling `shutdown_waits_for()` via `ensure_future()`"""

    items = []

    async def corofn():
        await asyncio.sleep(0.02)
        items.append(True)

    async def main():
        # Note that we don't need a loop variable anywhere!
        asyncio.ensure_future(shutdown_waits_for(corofn()))

    kill(SIGTERM, after=0.01)
    newloop()
    run(main())
    assert items


@pytest.mark.filterwarnings("ignore:coroutine 'shutdown_waits_for.<locals>.inner' was never awaited")
def test_sigterm_enduring_bare():
    """Call `shutdown_waits_for() without await, or create_task(), or
    ensure_future(). It's just bare. This actually works (because of
    the hidden Task inside). However, there will be a RuntimeWarning
    that the coroutine returned from `shutdown_waits_for() was never
    awaited. Therefore, maybe best not to use this, just to avoid
    confusion."""

    items = []

    async def corofn():
        await asyncio.sleep(0.02)
        items.append(True)

    async def main():
        shutdown_waits_for(corofn())  # <-- Look Ma! No awaits!

    kill(SIGTERM, after=0.01)
    newloop()
    run(main())
    assert items


def test_sigterm_enduring_await():
    """Call `shutdown_waits_for() with await."""
    items = []

    async def corofn(sleep):
        """This is the cf that must not be cancelled."""
        await asyncio.sleep(sleep)
        items.append(True)
        return True

    async def main():
        try:
            # This one is fast enough to finish
            out = await shutdown_waits_for(corofn(sleep=0.01))
            assert out is True

            # This one is going to last longer than the shutdown
            # but we can't get anything back out if that happens.
            await shutdown_waits_for(corofn(sleep=0.03))
            # main() gets cancelled here
            await asyncio.sleep(2)  # pragma: no cover.
            # This append won't happen
            items.append(True)  # pragma: no cover.
        except asyncio.CancelledError:
            print('main got cancelled')
            raise

    kill(SIGTERM, after=0.02)
    newloop()
    run(main())

    assert len(items) == 2


def test_sigterm_enduring_indirect_cancel():
    items = []

    async def corofn(sleep):
        await asyncio.sleep(sleep)
        # These next lines can't be hit, because of the cancellation.
        items.append(True)  # pragma: no cover
        return True  # pragma: no cover

    def direct_cancel():
        """Reach inside, find the one task that is marked "do not cancel"
        for shutdown, and then cancel it directly. This should raise
        CancelledError at the location of the caller for
        `shutdown_waits_for()`."""
        tasks = asyncio.Task.all_tasks()
        for t in tasks:  # pragma: no cover
            if t._coro in _DO_NOT_CANCEL_COROS:
                t.cancel()
                return

    async def main():
        loop = asyncio.get_event_loop()
        coro = corofn(sleep=0.02)
        loop.call_later(0.01, direct_cancel)
        with pytest.raises(asyncio.CancelledError):
            await shutdown_waits_for(coro)

    kill(SIGTERM, after=0.03)
    newloop()
    run(main())

    assert len(items) == 0
