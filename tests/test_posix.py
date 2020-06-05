import sys
import asyncio
import os
import time
import threading
from signal import SIGINT, SIGTERM
from concurrent.futures import ThreadPoolExecutor
from aiorun import run, shutdown_waits_for, _DO_NOT_CANCEL_COROS
import pytest
import multiprocessing as mp
from contextlib import contextmanager

# asyncio.Task.all_tasks is deprecated in favour of asyncio.all_tasks in Py3.7
try:
    from asyncio import all_tasks
except ImportError:
    all_tasks = asyncio.Task.all_tasks

if sys.platform == "win32":
    pytest.skip("Windows doesn't use POSIX signals", allow_module_level=True)


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


def main(q: mp.Queue):
    async def main():
        q.put("ok")
        await asyncio.sleep(5.0)

    run(main())


@pytest.fixture()
def mpproc():
    @contextmanager
    def run_proc(target):
        q = mp.Queue()
        p = mp.Process(target=target, args=(q,))
        p.start()
        try:
            yield p, q
        finally:
            p.close()

    return run_proc


@pytest.mark.parametrize("signal", [SIGTERM, SIGINT])
def test_sigterm_mp(mpproc, signal):
    """Basic SIGTERM"""

    with mpproc(target=main) as (p, q):
        ok = q.get()
        assert ok == "ok"
        os.kill(p.pid, SIGTERM)
        p.join(1.0)
        assert not p.is_alive()
        assert p.exitcode == 0


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
    run()


def test_exe():
    """Custom executor"""
    exe = ThreadPoolExecutor()
    kill(SIGTERM)
    run(executor=exe)


def test_sig_pause():
    """Trying to send multiple signals."""
    items = []

    async def main():
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            await asyncio.sleep(0.1)
            items.append(True)

    # The first sigint triggers shutdown mode, so all tasks are cancelled.
    # Note that main() catches CancelledError, and does a little bit more
    # work before exiting.
    kill(SIGINT, after=0.04)
    # The second sigint should have no effect, because aiorun signal
    # handler disables itself after the first sigint received, above.
    kill(SIGINT, after=0.08)
    run(main())
    assert items  # Verify that main() ran till completion.


def test_sigint():
    """Basic SIGINT"""
    kill(SIGINT)
    run()


def test_sigterm_enduring_create_task():
    """Calling `shutdown_waits_for()` via `create_task()`"""

    items = []

    async def corofn():
        await asyncio.sleep(0.04)
        items.append(True)

    async def main():
        loop = asyncio.get_event_loop()
        loop.create_task(shutdown_waits_for(corofn()))

    kill(SIGTERM, after=0.02)
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
    run(main())
    assert items


@pytest.mark.filterwarnings(
    "ignore:coroutine 'shutdown_waits_for.<locals>.inner' was never awaited"
)
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
            print("main got cancelled")
            raise

    kill(SIGTERM, after=0.02)
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
        tasks = all_tasks()
        for t in tasks:  # pragma: no cover
            if t._coro in _DO_NOT_CANCEL_COROS:
                t.cancel()
                return

    async def main():
        loop = asyncio.get_event_loop()
        coro = corofn(sleep=0.2)
        loop.call_later(0.1, direct_cancel)
        with pytest.raises(asyncio.CancelledError):
            await shutdown_waits_for(coro)

    kill(SIGTERM, after=0.3)
    run(main())

    assert len(items) == 0


def test_shutdown_callback():
    graceful = False
    fut = None

    async def _main():
        nonlocal fut, graceful

        fut = asyncio.Future()
        await fut

        graceful = True

    async def main():
        await shutdown_waits_for(_main())

    def shutdown_callback(loop):
        fut.set_result(None)

    kill(SIGTERM, 0.3)
    run(main(), shutdown_callback=shutdown_callback)

    assert graceful


def test_shutdown_callback_error():
    async def main():
        await asyncio.sleep(1e-3)

    def shutdown_callback(loop):
        raise Exception("blah")

    kill(SIGTERM, 0.3)
    with pytest.raises(Exception, match="blah"):
        run(main(), shutdown_callback=shutdown_callback)


def test_shutdown_callback_error_and_main_error(caplog):
    async def main():
        await asyncio.sleep(1e-3)
        raise Exception("main")

    def shutdown_callback(loop):
        raise Exception("blah")

    kill(SIGTERM, 0.3)
    with pytest.raises(Exception, match="main"):
        run(main(), stop_on_unhandled_errors=True, shutdown_callback=shutdown_callback)

    assert any(
        "shutdown_callback() raised an error" in r.message for r in caplog.records
    )


def test_mutex_loop_and_use_uvloop():
    async def main():
        pass

    with pytest.raises(Exception, match="mutually exclusive"):
        run(main(), loop=newloop(), use_uvloop=True)


def test_mutex_exc_handler_and_stop_unhandled():
    async def main():
        pass

    loop = newloop()
    loop.set_exception_handler(lambda loop, ctx: None)

    with pytest.raises(Exception, match="parameter is unavailable"):
        run(main(), loop=loop, stop_on_unhandled_errors=True)
