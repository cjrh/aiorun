"""Boilerplate for asyncio applications"""
import sys
import logging
import asyncio
from asyncio import (
    get_event_loop,
    AbstractEventLoop,
    Task,
    gather,
    CancelledError,
)
from concurrent.futures import Executor, ThreadPoolExecutor
import signal
from signal import SIGTERM, SIGINT
from typing import Optional, Callable
try:  # pragma: no cover
    # Coroutine only arrived in Python 3.5.3, and Ubuntu 16.04 is unfortunately
    # stuck on 3.5.2 for the time being. Revisit this in a year.
    from typing import Coroutine
except ImportError:  # pragma: no cover
    pass
from weakref import WeakSet
from functools import partial


__all__ = ['run', 'shutdown_waits_for']
__version__ = '2019.4.1'
logger = logging.getLogger('aiorun')
WINDOWS = sys.platform == 'win32'

try:
    # asyncio.Task.all_tasks is deprecated in favour of asyncio.all_tasks
    # in Python 3.7; remove ImportError handling when we drop support for
    # 3.6.
    from asyncio import all_tasks
except ImportError:
    all_tasks = Task.all_tasks


_DO_NOT_CANCEL_COROS = WeakSet()


def shutdown_waits_for(coro, loop=None):
    """Prevent coro from being cancelled during the shutdown sequence.

    The trick here is that we add this coro to the global
    "DO_NOT_CANCEL" collection, and then later during the shutdown
    sequence we make sure that the task that wraps this coro will NOT
    be cancelled.

    To make this work, we have to create a super-secret task, below, that
    communicates with the caller (which "awaits" us) via a Future. Using
    a Future in this way allows us to avoid awaiting the Task, which
    decouples the Task from the normal exception propagation which would
    normally happen when the outer Task gets cancelled.  We get the
    result of coro back to the caller via Future.set_result.

    NOTE that during the shutdown sequence, the caller WILL NOT be able
    to receive a result, since the caller will likely have been
    cancelled.  So you should probably not rely on capturing results
    via this function.
    """
    loop = loop or get_event_loop()
    fut = loop.create_future()  # This future will connect coro and the caller.

    async def coro_proxy():
        """This function will await coro, but it will also send the result
        over the the future. Remember: the outside caller (of
        shutdown_waits_for) will be awaiting fut, NOT coro(), due to
        the decoupling. However, when coro completes, we need to send its
        result over to the fut to make it look *as if* it was just coro
        running the whole time. This whole thing is a teeny magic trick.
        """
        try:
            result = await coro
        except (CancelledError, Exception) as e:
            set_fut_done = partial(fut.set_exception, e)
        else:
            set_fut_done = partial(fut.set_result, result)

        if not fut.cancelled():
            set_fut_done()

    new_coro = coro_proxy()  # We'll taskify this one instead of coro.
    _DO_NOT_CANCEL_COROS.add(new_coro)  # The new task must not be cancelled.
    loop.create_task(new_coro)  # Make the task

    # Ok, so we *could* simply return fut.  Callers can await it as normal,
    # e.g.
    #
    # async def blah():
    #   x = await shutdown_waits_for(bleh())
    #
    # That will work fine.  However, callers may *also* want to detach the
    # call from the current execution context, e.g.
    #
    # async def blah():
    #   loop.create_task(shutdown_waits_for(bleh()))
    #
    # This will only work if shutdown_waits_for() returns a coroutine.
    # Therefore, we just make a new coroutine to wrap the `await fut` and
    # return that.  Then both things will work.
    #
    # (Side note: instead of callers using create_tasks, it would also work
    # if they used `asyncio.ensure_future()` instead, since that can work
    # with futures. But I don't like ensure_future.)
    #
    # (Another side note: You don't even need `create_task()` or
    # `ensure_future()`...If you don't want a result, you can just call
    # `shutdown_waits_for()` as a flat function call, no await or anything,
    # and it should still work; unfortunately it causes a RuntimeWarning to
    # tell you that ``inner()`` was never awaited :/

    async def inner():
        return await fut

    return inner()


def run(coro: 'Optional[Coroutine]' = None, *,
        loop: Optional[AbstractEventLoop] = None,
        shutdown_handler: Optional[Callable[[AbstractEventLoop], None]] = None,
        executor_workers: int = 10,
        executor: Optional[Executor] = None,
        use_uvloop: bool = False) -> None:
    """
    Start up the event loop, and wait for a signal to shut down.

    :param coro: Optionally supply a coroutine. The loop will still
        run if missing. The loop will continue to run after the supplied
        coroutine finishes. The supplied coroutine is typically
        a "main" coroutine from which all other work is spawned.
    :param loop: Optionally supply your own loop. If missing, the
        default loop attached to the current thread context will
        be used, i.e., whatever ``asyncio.get_event_loop()`` returns.
    :param shutdown_handler: By default, SIGINT and SIGTERM will be
        handled and will stop the loop, thereby invoking the shutdown
        sequence. Alternatively you can supply your own shutdown
        handler function. It should conform to the type spec as shown
        in the function signature.
    :param executor_workers: The number of workers in the executor.
        (NOTE: ``run()`` creates a new executor instance internally,
        regardless of whether you supply your own loop.)
    :param executor: You can decide to use your own executor instance
        if you like.
    :param use_uvloop: The loop policy will be set to use uvloop. It
        is your responsibility to install uvloop. If missing, an
        ``ImportError`` will be raised.
    """
    logger.debug('Entering run()')

    assert not (loop and use_uvloop), (
        "'loop' and 'use_uvloop' parameters are mutually "
        "exclusive. (Just make your own uvloop and pass it in)."
    )
    if use_uvloop:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    loop_was_supplied = bool(loop)
    if not loop_was_supplied:
        loop = get_event_loop()

    if coro:
        async def new_coro():
            """During shutdown, run_until_complete() will exit
            if a CancelledError bubbles up from anything in the
            group. To counteract that, we'll try to handle
            any CancelledErrors that bubble up from the given
            coro. This isn't fool-proof: if the user doesn't
            provide a coro, and instead creates their own with
            loop.create_task, that task might bubble
            a CancelledError into the run_until_complete()."""
            try:
                await coro
            except asyncio.CancelledError:
                pass
        loop.create_task(new_coro())

    shutdown_handler = shutdown_handler or _shutdown_handler

    if WINDOWS:  # pragma: no cover
        # This is to allow CTRL-C to be detected in a timely fashion,
        # see: https://bugs.python.org/issue23057#msg246316
        loop.create_task(windows_support_wakeup())

        # This is to be able to handle SIGBREAK.
        def windows_handler(sig, frame):
            # Disable the handler so it won't be called again.
            signame = signal.Signals(sig).name
            logger.critical('Received signal: %s. Stopping the loop.', signame)
            shutdown_handler(loop)

        signal.signal(signal.SIGBREAK, windows_handler)
        signal.signal(signal.SIGINT, windows_handler)
    else:
        loop.add_signal_handler(SIGINT, shutdown_handler, loop)
        loop.add_signal_handler(SIGTERM, shutdown_handler, loop)

    # TODO: We probably don't want to create a different executor if the
    # TODO: loop was supplied. (User might have put stuff on that loop's
    # TODO: executor).
    if not executor:
        logger.debug('Creating default executor')
        executor = ThreadPoolExecutor(max_workers=executor_workers)
    loop.set_default_executor(executor)
    try:
        loop.run_forever()
    except KeyboardInterrupt:  # pragma: no cover
        logger.info('Got KeyboardInterrupt')
        if WINDOWS:
            # Windows doesn't do any POSIX signal handling, and no
            # abstraction layer for signals is currently implemented in
            # asyncio. So we fall back to KeyboardInterrupt (triggered
            # by the user/environment sending CTRL-C, or signal.CTRL_C_EVENT
            shutdown_handler()
    logger.info('Entering shutdown phase.')

    def sep():
        tasks = all_tasks(loop=loop)
        do_not_cancel = set()
        for t in tasks:
            # TODO: we don't need access to the coro. We could simply
            # TODO: store the task itself in the weakset.
            if t._coro in _DO_NOT_CANCEL_COROS:
                do_not_cancel.add(t)

        tasks -= do_not_cancel

        logger.info('Cancelling pending tasks.')
        for t in tasks:
            logger.debug('Cancelling task: %s', t)
            t.cancel()
        return tasks, do_not_cancel

    tasks, do_not_cancel = sep()
    # Here's a protip: if you group a bunch of tasks, and some of them
    # get cancelled, and they DON'T HANDLE THE CANCELLATION, then the
    # raised CancelledError will bubble up to, and stop the
    # loop.run_until_complete() line: meaning, not all the tasks in
    # the gathered group will actually be complete. You need to
    # enable this with the ``return_exceptions`` flag.
    group = gather(*tasks, *do_not_cancel, return_exceptions=True)
    logger.info('Running pending tasks till complete')
    # TODO: obtain all the results, and log any results that are exceptions
    # other than CancelledError. Will be useful for troubleshooting.
    loop.run_until_complete(group)

    logger.info('Waiting for executor shutdown.')
    executor.shutdown(wait=True)
    # If loop was supplied, it's up to the caller to close!
    if not loop_was_supplied:
        logger.info('Closing the loop.')
        loop.close()
    logger.critical('Leaving. Bye!')


async def windows_support_wakeup():  # pragma: no cover
    """See https://stackoverflow.com/a/36925722 """
    while True:
        await asyncio.sleep(0.1)


def _shutdown_handler(loop):
    logger.debug('Entering shutdown handler')
    loop = loop or get_event_loop()

    # Disable the handlers so they won't be called again.
    if WINDOWS:  # pragma: no cover
        # These calls to signal.signal can only be called from the main
        # thread.
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    else:
        loop.remove_signal_handler(SIGTERM)
        loop.add_signal_handler(SIGINT, lambda: None)

    logger.critical('Stopping the loop')
    loop.call_soon_threadsafe(loop.stop)
