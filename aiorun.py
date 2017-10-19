from asyncio import (
    get_event_loop,
    AbstractEventLoop,
    Task,
    gather,
    CancelledError,
)
from concurrent.futures import ThreadPoolExecutor as Executor
from signal import SIGTERM, SIGINT
from typing import Optional, Coroutine


__all__ = ['run']


def shutdown():
    loop: AbstractEventLoop = get_event_loop()
    loop.remove_signal_handler(SIGTERM)
    loop.add_signal_handler(SIGINT, lambda: None)
    loop.stop()


def run(coro: Optional[Coroutine]):
    loop: AbstractEventLoop = get_event_loop()
    if coro:
        loop.create_task(coro)
    loop.add_signal_handler(SIGINT, shutdown)
    executor = Executor(max_workers=10)
    loop.set_default_executor(executor)
    loop.run_forever()
    tasks = Task.all_tasks()
    group = gather(*tasks)
    group.cancel()
    try:
        loop.run_until_complete(group)
    except CancelledError:
        pass
    executor.shutdown(wait=True)
    loop.close()
