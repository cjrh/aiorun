"""Boilerplate for asyncio applications"""
import logging
from asyncio import (
    get_event_loop,
    set_event_loop,
    new_event_loop,
    AbstractEventLoop,
    Task,
    gather,
    CancelledError,
)
from concurrent.futures import Executor, ThreadPoolExecutor
from signal import SIGTERM, SIGINT
from typing import Optional, Coroutine, Callable


__all__ = ['run']
__version__ = '2017.10.2'
logger = logging.getLogger('aiorun')


def shutdown():
    logger.debug('Entering shutdown handler')
    loop = get_event_loop()
    loop.remove_signal_handler(SIGTERM)
    loop.add_signal_handler(SIGINT, lambda: None)
    logger.critical('Stopping the loop')
    loop.stop()


def run(coro: Optional[Coroutine] = None, *,
        loop: Optional[AbstractEventLoop] = None,
        shutdown_handler: Callable[[], None] = shutdown,
        executor_workers: int = 10,
        executor: Optional[Executor] = None) -> None:
    logger.debug('Entering run()')
    if not loop:
        loop = new_event_loop()
        set_event_loop(loop)
    if coro:
        loop.create_task(coro)
    loop.add_signal_handler(SIGINT, shutdown_handler)
    loop.add_signal_handler(SIGTERM, shutdown_handler)
    if not executor:
        logger.debug('Creating default executor')
        executor = ThreadPoolExecutor(max_workers=executor_workers)
    loop.set_default_executor(executor)
    logger.critical('Running forever.')
    loop.run_forever()
    logger.critical('Entering shutdown phase.')
    tasks = Task.all_tasks()
    group = gather(*tasks)
    logger.critical('Cancelling pending tasks.')
    group.cancel()
    try:
        logger.critical('Running pending tasks till complete')
        loop.run_until_complete(group)
    except CancelledError:
        pass
    logger.critical('Waiting for executor shutdown.')
    executor.shutdown(wait=True)
    logger.critical('Closing the loop.')
    loop.close()
    logger.critical('Leaving. Bye!')
