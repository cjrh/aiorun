import sys
import asyncio
import os
import time
from signal import SIGINT, SIGTERM
from concurrent.futures import ThreadPoolExecutor
from typing import List
from enum import Enum


from aiorun import run, shutdown_waits_for, _DO_NOT_CANCEL_COROS, ShutdownCallback
import pytest
import multiprocessing as mp
from contextlib import contextmanager


# asyncio.Task.all_tasks is deprecated in favour of asyncio.all_tasks in Py3.7
try:
    from asyncio import all_tasks
except ImportError:  # pragma: no cover
    all_tasks = asyncio.Task.all_tasks

if sys.platform == "win32":  # pragma: no cover
    pytest.skip("Windows doesn't use POSIX signals", allow_module_level=True)


def newloop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


@pytest.fixture()
def mpproc():
    @contextmanager
    def run_proc(target, expected_exit_code=0, allowed_shutdown_delay=5.0, **kwargs):
        q = mp.JoinableQueue()
        p = mp.Process(target=target, args=(q,), kwargs=kwargs)
        p.start()
        try:
            yield p, q
        finally:
            # Wait a bit for the subprocess to finish.
            p.join(allowed_shutdown_delay)
            # The subprocess should never be alive here. If it is, kill
            # it and raise.
            if p.is_alive():
                p.terminate()
                raise ValueError("Process was alive but should not be.")

            if p.exitcode != expected_exit_code:
                raise ValueError("Process exitcode was %s" % p.exitcode)

            if sys.version_info >= (3, 7):
                p.close()

    return run_proc


def main(q: mp.Queue, **kwargs):
    async def main():
        q.put("ok")
        await asyncio.sleep(5.0)

    if kwargs.pop("use_exe", None):
        exe = ThreadPoolExecutor()
    else:
        exe = None

    loop = None
    if kwargs.pop("user_supplied_loop", None) and not kwargs.get("use_uvloop"):
        loop = newloop()

    try:
        run(main(), executor=exe, loop=loop, **kwargs)
    finally:
        q.put(None)
        q.join()


@pytest.mark.parametrize("user_supplied_loop", [False, True])
@pytest.mark.parametrize("use_exe", [False, True])
@pytest.mark.parametrize("use_uvloop", [False, True])
@pytest.mark.parametrize("signal", [SIGTERM, SIGINT])
def test_sigterm_mp(mpproc, signal, use_uvloop, use_exe, user_supplied_loop):
    """Basic SIGTERM"""

    with mpproc(
        target=main,
        use_uvloop=use_uvloop,
        use_exe=use_exe,
        user_supplied_loop=user_supplied_loop,
    ) as (
        p,
        q,
    ):
        ok = q.get()
        q.task_done()
        assert ok == "ok"
        os.kill(p.pid, signal)
        items = drain_queue(q)
        assert not items


def main_no_coro(q: mp.Queue):
    run()
    q.put(None)
    q.join()


def test_no_coroutine(mpproc):
    """Signal should still work without a main coroutine"""
    with mpproc(target=main_no_coro) as (p, q):
        # TODO: with multiprocessing set_start_method=spawn, lowering this
        #  detail causes hanging. Still unclear why.
        time.sleep(0.8)
        os.kill(p.pid, SIGTERM)
        assert drain_queue(q) == []


def main_sig_pause(q: mp.Queue):
    async def main():
        try:
            q.put_nowait("ok")
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            print("in cancellation handler")
            await asyncio.sleep(0.1)

    run(main())
    q.put("done")
    q.put(None)
    q.join()


def drain_queue(q: mp.JoinableQueue) -> List:
    """Keeps taking items until we get a `None`."""
    items = []
    item = q.get()
    q.task_done()
    while item is not None:
        items.append(item)
        item = q.get()
        q.task_done()

    return items


@pytest.mark.parametrize("pre_sig_delay", [0, 0.001, 0.05])
@pytest.mark.parametrize("post_sig_delay", [0, 0.001, 0.05])
@pytest.mark.parametrize("signal", [SIGTERM, SIGINT])
def test_sig_pause_mp(mpproc, signal, pre_sig_delay, post_sig_delay):
    with mpproc(target=main_sig_pause) as (p, q):

        # async main function is ready
        ok = q.get()
        q.task_done()
        assert ok == "ok"

        # Send the first signal
        time.sleep(pre_sig_delay)
        os.kill(p.pid, signal)

        # Wait a bit then send more signals - these should be ignored
        time.sleep(post_sig_delay)
        os.kill(p.pid, signal)
        os.kill(p.pid, signal)
        os.kill(p.pid, signal)

        # Collect items sent back on the q until we get `None`
        items = drain_queue(q)
        assert items == ["done"]


def main_sigterm_enduring_create_task(q: mp.Queue, spawn_method):
    async def corofn():
        q.put_nowait("ok")
        await asyncio.sleep(0.05)
        q.put_nowait(True)

    async def main():
        loop = asyncio.get_event_loop()
        if spawn_method == "create_task":
            loop.create_task(shutdown_waits_for(corofn()))
        elif spawn_method == "ensure_future":
            asyncio.ensure_future(shutdown_waits_for(corofn()))
        elif spawn_method == "await":
            await shutdown_waits_for(corofn())
        elif spawn_method == "bare":
            shutdown_waits_for(corofn())

    run(main())
    q.put(None)
    q.join()


@pytest.mark.parametrize(
    "spawn_method", ["create_task", "ensure_future", "await", "bare"]
)
@pytest.mark.parametrize("signal", [SIGTERM, SIGINT])
def test_sigterm_enduring_create_task(mpproc, signal, spawn_method):
    """Calling `shutdown_waits_for()` via `create_task()`"""
    with mpproc(
        target=main_sigterm_enduring_create_task, spawn_method=spawn_method
    ) as (p, q):
        # async main function is ready
        ok = q.get()
        q.task_done()
        assert ok == "ok"

        # signal and collect
        time.sleep(0.01)
        os.kill(p.pid, signal)
        items = drain_queue(q)
        assert items == [True]


def main_sigterm_enduring_indirect_cancel(q: mp.Queue):
    async def corofn():
        q.put_nowait("ok")
        await asyncio.sleep(0.2)
        q.put_nowait(True)

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
        coro = corofn()
        loop.call_later(0.1, direct_cancel)
        try:
            await shutdown_waits_for(coro)
        except asyncio.CancelledError:
            q.put_nowait("got cancellation as expected")
        else:
            q.put_nowait("no cancellation raised")

    run(main())
    q.put(None)
    q.join()


def test_sigterm_enduring_indirect_cancel(mpproc):
    with mpproc(target=main_sigterm_enduring_indirect_cancel) as (p, q):
        # async main function is ready
        ok = q.get()
        q.task_done()
        assert ok == "ok"  # Means the coro inside shutdown_waits_for is up.

        # signal and collect
        time.sleep(0.01)
        os.kill(p.pid, SIGTERM)
        items = drain_queue(q)
        assert items == ["got cancellation as expected"]


class CallbackType(Enum):
    FUNCTION = 1
    ASYNC_DEF_FUNCTION = 2
    COROUTINE_OBJECT = 3


def make_shutdown_callback(cbtype: CallbackType, fut: asyncio.Future) -> ShutdownCallback:
    if cbtype is CallbackType.FUNCTION:
        def shutdown_callback(loop):
            print('shutdown callback called!')
            fut.set_result(None)

        return shutdown_callback

    elif cbtype is CallbackType.ASYNC_DEF_FUNCTION:
        async def shutdown_callback(loop):
            fut.set_result(None)

        return shutdown_callback

    elif cbtype is CallbackType.COROUTINE_OBJECT:
        async def shutdown_callback(loop):
            fut.set_result(None)

        return shutdown_callback(None)

    else:
        raise TypeError('Unexpected callback type.')


def main_shutdown_callback(q: mp.Queue, *, cbtype: "ShutdownCallback"):
    async def main():
        # Inform the test caller that the main coro is ready
        q.put_nowait("ok")
        await asyncio.sleep(10.0)
        # Inform the test caller that the fut was unblocked successfully.
        q.put_nowait(True)

    if cbtype is CallbackType.FUNCTION:
        def shutdown_callback(loop):
            q.put_nowait(True)
    elif cbtype is CallbackType.ASYNC_DEF_FUNCTION:
        async def shutdown_callback(loop):
            q.put_nowait(True)
    elif cbtype is CallbackType.COROUTINE_OBJECT:
        async def shutdown_callback_fn(loop):
            q.put_nowait(True)

        shutdown_callback = shutdown_callback_fn(None)
    else:
        raise TypeError('Unexpected cbtype')

    run(main(), shutdown_callback=shutdown_callback)
    q.put(None)
    q.join()


@pytest.mark.parametrize('cbtype', [
    CallbackType.FUNCTION,
    CallbackType.ASYNC_DEF_FUNCTION,
    CallbackType.COROUTINE_OBJECT,
])
def test_shutdown_callback(mpproc, cbtype):
    with mpproc(target=main_shutdown_callback, cbtype=cbtype) as (p, q):

        # async main function is ready
        ok = q.get()
        q.task_done()
        assert ok == "ok"

        # signal and collect
        time.sleep(0.3)
        os.kill(p.pid, SIGTERM)
        items = drain_queue(q)
        assert items == [True]


def main_shutdown_callback_error(q: mp.Queue):
    async def main():
        await asyncio.sleep(1e-3)

    def shutdown_callback(loop):
        raise Exception("blah")

    try:
        run(main(), shutdown_callback=shutdown_callback)
    except Exception as e:
        q.put_nowait(e)
    else:
        q.put_nowait("exception was not raised")
    finally:
        q.put_nowait(None)
        q.join()


def test_shutdown_callback_error(mpproc):
    with mpproc(target=main_shutdown_callback_error) as (p, q):
        # TODO: with multiprocessing set_start_method=spawn, lowering this
        #  detail causes hanging. Still unclear why.
        time.sleep(0.8)
        os.kill(p.pid, SIGTERM)
        items = drain_queue(q)
        assert isinstance(items[0], Exception)
        assert str(items[0]) == "blah"


def main_shutdown_callback_error_and_main_error(q: mp.Queue):

    import logging

    log_messages = []

    def filt(record):
        log_messages.append(record.getMessage())
        return record

    logging.getLogger("aiorun").addFilter(filt)

    async def main():
        await asyncio.sleep(1e-3)
        raise Exception("main")

    def shutdown_callback(loop):
        raise Exception("blah")

    try:
        run(main(), stop_on_unhandled_errors=True, shutdown_callback=shutdown_callback)
    except Exception as e:
        q.put_nowait(e)
    else:
        q.put_nowait("exception was not raised")
    finally:
        q.put_nowait(log_messages)
        q.put_nowait(None)
        q.join()


def test_shutdown_callback_error_and_main_error(mpproc):
    with mpproc(
        target=main_shutdown_callback_error_and_main_error, expected_exit_code=1
    ) as (p, q):
        # TODO: with multiprocessing set_start_method=spawn, lowering this
        #  detail causes hanging. Still unclear why.
        time.sleep(0.8)
        os.kill(p.pid, SIGTERM)
        items = drain_queue(q)
        assert isinstance(items[0], Exception)
        assert str(items[0]) == "main"

        records = items[1]
        print(records)
        assert any("shutdown_callback() raised an error" in r for r in records)


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
