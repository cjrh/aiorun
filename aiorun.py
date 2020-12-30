"""Boilerplate for asyncio applications"""
import signal
import sys
import logging
import inspect
import asyncio
from asyncio import get_event_loop, AbstractEventLoop, Task, gather, CancelledError
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Optional, Callable, Union, Any

try:  # pragma: no cover
    # Coroutine only arrived in Python 3.5.3, and Ubuntu 16.04 is unfortunately
    # stuck on 3.5.2 for the time being. Revisit this in a year.
    from typing import Coroutine, Awaitable

    ShutdownCallback = Optional[
        Union[
            Awaitable,
            Callable[[AbstractEventLoop], Awaitable],
            Callable[[AbstractEventLoop], None],
        ]
    ]

except ImportError:  # pragma: no cover
    Coroutine = Any
    Awaitable = Any
    ShutdownCallback = Any

from weakref import WeakSet
from functools import partial


__all__ = ["run", "shutdown_waits_for"]
__version__ = "2020.12.1"
logger = logging.getLogger("aiorun")
WINDOWS = sys.platform == "win32"

try:
    # asyncio.Task.all_tasks is deprecated in favour of asyncio.all_tasks
    # in Python 3.7; remove ImportError handling when we drop support for
    # 3.6.
    from asyncio import all_tasks
except ImportError:  # pragma: no cover
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
        over to the future. Remember: the outside caller (of
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


def run(
    coro: "Optional[Coroutine]" = None,
    *,
    loop: Optional[AbstractEventLoop] = None,
    shutdown_handler: Optional[Callable[[AbstractEventLoop], None]] = None,
    shutdown_callback: "ShutdownCallback" = None,
    executor_workers: int = 10,
    executor: Optional[Executor] = None,
    use_uvloop: bool = False,
    stop_on_unhandled_errors: bool = False
) -> None:
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
    :param shutdown_callback: Callable, executed after loop is stopped, before
        cancelling any tasks.
        Useful for graceful shutdown.
    :param executor_workers: The number of workers in the executor.
        (NOTE: ``run()`` creates a new executor instance internally,
        regardless of whether you supply your own loop.)
    :param executor: You can decide to use your own executor instance
        if you like.
    :param use_uvloop: The loop policy will be set to use uvloop. It
        is your responsibility to install uvloop. If missing, an
        ``ImportError`` will be raised.
    :param stop_on_unhandled_errors: By default, the event loop will
        handle any exceptions that get raised and are not handled. This
        means that the event loop will continue running regardless of errors,
        and the only way to stop it is to call `loop.stop()`. However, if
        this flag is set, any unhandled exceptions will stop the loop, and
        be re-raised after the normal shutdown sequence is completed.
    """
    _clear_signal_handlers()
    logger.debug("Entering run()")
    # Disable default signal handling ASAP

    if loop and use_uvloop:
        raise Exception(
            "'loop' and 'use_uvloop' parameters are mutually "
            "exclusive. (Just make your own uvloop and pass it in)."
        )

    loop_was_supplied = bool(loop)

    if not loop_was_supplied:
        if use_uvloop:
            import uvloop

            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        else:
            asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop and loop.get_exception_handler() and stop_on_unhandled_errors:
        raise Exception(
            "If you provide a loop instance, and you've configured a custom "
            "exception handler on it, then the 'stop_on_unhandled_errors' "
            "parameter is unavailable (all exceptions will be handled)."
        )

    pending_exception_to_raise = None

    def custom_exception_handler(loop, context: dict):
        """See: https://docs.python.org/3/library/asyncio-eventloop.html#error-handling-api"""
        nonlocal pending_exception_to_raise
        pending_exception_to_raise = context.get("exception")
        logger.error(
            "Unhandled exception; stopping loop: %r",
            context.get("message"),
            exc_info=pending_exception_to_raise
        )
        loop.stop()

    if stop_on_unhandled_errors:
        loop.set_exception_handler(custom_exception_handler)

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
    # Setting up signal handlers. The callback configured by the
    # underlying system (non-asyncio) API ``signal.signal`` is
    # pre-emptive, which means you can't safely do loop manipulation
    # with it; yet, aiorun provides an API that allows you to specify
    # a ``shutdown_handler`` that takes a loop parameter. This will be
    # used to manipulate the loop. How to bridge these two worlds?
    # Here we use a private, internal wrapper function that can be
    # called from ``signal.signal`` (i.e. pre-emptive interruption)
    # but which will call our actual, non-pre-emptive shutdown handler
    # in a safe way.
    #
    # This is supposed to be what loop.add_signal_handler does, but I
    # cannot seem get it to work robustly.
    sighandler = partial(_signal_wrapper, loop=loop, actual_handler=shutdown_handler)
    _set_signal_handlers(sighandler)

    if WINDOWS:  # pragma: no cover
        # This is to allow CTRL-C to be detected in a timely fashion,
        # see: https://bugs.python.org/issue23057#msg246316
        loop.create_task(windows_support_wakeup())

    # TODO: We probably don't want to create a different executor if the
    # TODO: loop was supplied. (User might have put stuff on that loop's
    # TODO: executor).
    if not executor:
        logger.debug("Creating default executor")
        executor = ThreadPoolExecutor(max_workers=executor_workers)
    loop.set_default_executor(executor)
    try:
        loop.run_forever()
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("Got KeyboardInterrupt")
        if WINDOWS:
            # Windows doesn't do any POSIX signal handling, and no
            # abstraction layer for signals is currently implemented in
            # asyncio. So we fall back to KeyboardInterrupt (triggered
            # by the user/environment sending CTRL-C, or signal.CTRL_C_EVENT
            shutdown_handler(loop)
    logger.info("Entering shutdown phase.")

    if shutdown_callback is not None:
        logger.info("Executing provided shutdown_callback.")
        try:
            if inspect.iscoroutine(shutdown_callback):
                loop.run_until_complete(shutdown_callback)
            elif inspect.iscoroutinefunction(shutdown_callback):
                loop.run_until_complete(shutdown_callback(loop))
            elif callable(shutdown_callback):
                shutdown_callback(loop)
            else:
                raise TypeError(
                    "The provided shutdown_callback must be either a function,"
                    "an awaitable, or a coroutine object, but it was "
                    + str(type(shutdown_callback))
                )
        except BaseException as exc:
            if pending_exception_to_raise:
                logger.exception(
                    "The shutdown_callback() raised an error, but there is "
                    "already a different exception raised from the loop, so "
                    "this log message is all you're going to see about it."
                )
            else:
                pending_exception_to_raise = exc

    def sep():
        tasks = all_tasks(loop=loop)
        do_not_cancel = set()
        for t in tasks:
            # TODO: we don't need access to the coro. We could simply
            # TODO: store the task itself in the weakset.
            if t._coro in _DO_NOT_CANCEL_COROS:
                do_not_cancel.add(t)

        tasks -= do_not_cancel

        logger.info("Cancelling pending tasks.")
        for t in tasks:
            logger.debug("Cancelling task: %s", t)
            t.cancel()
        return tasks, do_not_cancel

    tasks, do_not_cancel = sep()
    # Here's a protip: if you group a bunch of tasks, and some of them
    # get cancelled, and they DON'T HANDLE THE CANCELLATION, then the
    # raised CancelledError will bubble up to, and stop the
    # loop.run_until_complete() line: meaning, not all the tasks in
    # the gathered group will actually be complete. You need to
    # enable this with the ``return_exceptions`` flag.
    group = gather(*tasks, *do_not_cancel, loop=loop, return_exceptions=True)
    logger.info("Running pending tasks till complete")
    # TODO: obtain all the results, and log any results that are exceptions
    # other than CancelledError. Will be useful for troubleshooting.
    loop.run_until_complete(group)

    logger.info("Waiting for executor shutdown.")
    executor.shutdown(wait=True)
    # If loop was supplied, it's up to the caller to close!
    if not loop_was_supplied:
        if sys.version_info >= (3, 6):
            logger.info("Shutting down async generators")
            loop.run_until_complete(loop.shutdown_asyncgens())
        logger.info("Closing the loop.")
        loop.close()
    logger.info("Leaving. Bye!")

    if pending_exception_to_raise:
        logger.info("Reraising unhandled exception")
        raise pending_exception_to_raise


async def windows_support_wakeup():  # pragma: no cover
    """See https://stackoverflow.com/a/36925722 """
    while True:
        await asyncio.sleep(0.1)


def _signal_wrapper(sig, frame, loop: asyncio.AbstractEventLoop, actual_handler):
    """This private function does nothing other than call the actual signal
    handler function in a way that is safe for asyncio. This function is
    called as the raw signal handler which means it is called pre-emptively,
    that's why we used ``call_soon_threadsafe`` below. The actual signal
    handler can interact with the loop in a safe way."""
    # Disable the handlers so they won't be called again.
    _clear_signal_handlers()
    loop.call_soon_threadsafe(actual_handler, loop)


def _shutdown_handler(loop):
    logger.debug("Entering shutdown handler")
    loop = loop or get_event_loop()

    logger.critical("Stopping the loop")
    loop.stop()


def _set_signal_handlers(threadsafe_func):
    if WINDOWS:  # pragma: no cover
        signal.signal(signal.SIGBREAK, threadsafe_func)
        signal.signal(signal.SIGINT, threadsafe_func)
    else:
        signal.signal(signal.SIGTERM, threadsafe_func)
        signal.signal(signal.SIGINT, threadsafe_func)


def _clear_signal_handlers():
    if WINDOWS:  # pragma: no cover
        # These calls to signal.signal can only be called from the main
        # thread.
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
