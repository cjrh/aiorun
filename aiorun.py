"""Boilerplate for asyncio applications"""
import logging
import asyncio
from asyncio import (
    get_event_loop,
    AbstractEventLoop,
    Task,
    gather,
    CancelledError,
    Future
)
from concurrent.futures import Executor, ThreadPoolExecutor
from signal import SIGTERM, SIGINT
from typing import Optional, Coroutine, Callable
from weakref import WeakSet
from functools import partial


__all__ = ['run', 'shutdown_waits_for']
__version__ = '2017.11.6'
logger = logging.getLogger('aiorun')


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
    fut = Future(loop=loop)  # This future will connect coro and the caller.

    async def coro_proxy():
        """This function will await coro, but it will also send the result
        over the the future. Rememeber: the outside caller (of
        shutdown_waits_for) will be awaiting fut, NOT coro(), due to
        the decoupling. However, when coro completes, we need to send its
        result over to the fut to make it look *as if* it was just coro
        running the whole time. This whole thing is a teeny magic trick.
        """
        try:
            result = await coro
            try:
                fut.set_result(result)
            except asyncio.InvalidStateError:
                logger.warning('Failed to set result.')
        except CancelledError as e:
            fut.set_exception(e)

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


def _shutdown(loop=None):
    logger.debug('Entering shutdown handler')
    loop = loop or get_event_loop()
    loop.remove_signal_handler(SIGTERM)
    loop.add_signal_handler(SIGINT, lambda: None)
    logger.critical('Stopping the loop')
    loop.call_soon_threadsafe(loop.stop)


def run(coro: Optional[Coroutine] = None, *,
        loop: Optional[AbstractEventLoop] = None,
        shutdown_handler: Optional[
            Callable[[Optional[AbstractEventLoop]], None]] = None,
        executor_workers: int = 10,
        executor: Optional[Executor] = None,
        use_uvloop: bool = False) -> None:
    """
    Start up the event loop, and wait for a signal to shut down.

    :param coro: Optionally supply a coroutine. The loop will still
        run if missing. This would typically be a "main" coroutine
        from which all other work is spawned.
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

    shutdown_handler = shutdown_handler or partial(_shutdown, loop)
    loop.add_signal_handler(SIGINT, shutdown_handler)
    loop.add_signal_handler(SIGTERM, shutdown_handler)

    # TODO: We probably don't want to create a different executor if the
    # TODO: loop was supplied. (User might have put stuff on that loop's
    # TODO: executor).
    if not executor:
        logger.debug('Creating default executor')
        executor = ThreadPoolExecutor(max_workers=executor_workers)
    loop.set_default_executor(executor)
    logger.critical('Running forever.')
    loop.run_forever()
    logger.critical('Entering shutdown phase.')

    def sep():
        tasks = Task.all_tasks(loop=loop)
        do_not_cancel = set()
        for t in tasks:
            # TODO: we don't need access to the coro. We could simply
            # TODO: store the task itself in the weakset.
            if t._coro in _DO_NOT_CANCEL_COROS:
                do_not_cancel.add(t)

        tasks -= do_not_cancel

        logger.critical('Cancelling pending tasks.')
        for t in tasks:
            print('Cancelling task: ', t)
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
    logger.critical('Running pending tasks till complete')
    # TODO: obtain all the results, and log any results that are exceptions
    # other than CancelledError. Will be useful for troubleshooting.
    loop.run_until_complete(group)

    logger.critical('Waiting for executor shutdown.')
    executor.shutdown(wait=True)
    # If loop was supplied, it's up to the caller to close!
    if not loop_was_supplied:
        logger.critical('Closing the loop.')
        loop.close()
    logger.critical('Leaving. Bye!')
