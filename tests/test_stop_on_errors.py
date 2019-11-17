import asyncio

import pytest

from aiorun import run


def test_exc_stop():
    """Basic SIGTERM"""

    created_tasks = []

    async def background_task():
        await asyncio.sleep(10)

    async def main():
        # loop = asyncio.get_running_loop()
        loop = asyncio.get_event_loop()
        created_tasks.extend(loop.create_task(background_task()) for i in range(10))
        await asyncio.sleep(0.01)
        raise Exception("Stops the loop")

    with pytest.raises(Exception) as excinfo:
        run(main(), stop_on_unhandled_errors=True)

    print(excinfo.traceback)
    assert "Stops the loop" in str(excinfo.value)
    assert all(t.cancelled for t in created_tasks)
