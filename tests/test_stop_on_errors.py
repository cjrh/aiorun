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


class _TestException(Exception):
    pass


@pytest.mark.parametrize(
    "context, raised_exc",
    [
        ({"message": "test error"}, None),
        (
            {"message": "test error", "exception": _TestException("test error")},
            _TestException,
        ),
    ],
)
def test_call_exception_handler(context, raised_exc):
    """Test when loop's exception handler was called with custom context"""
    created_tasks = []

    async def background_task():
        await asyncio.sleep(2)

    async def main():
        loop = asyncio.get_event_loop()
        created_tasks.extend(loop.create_task(background_task()) for _ in range(5))
        await asyncio.sleep(0.1)
        loop.call_exception_handler(context=context)

    if raised_exc is not None:
        with pytest.raises(raised_exc) as excinfo:
            run(main(), stop_on_unhandled_errors=True)
            assert "test error" in str(excinfo.value)
    else:
        run(main(), stop_on_unhandled_errors=True)

    assert all(t.cancelled for t in created_tasks)


def test_stop_must_be_obeyed(caplog):
    """Basic SIGTERM"""

    created_tasks = []

    async def naughty_task():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(10)

    async def main():
        # loop = asyncio.get_running_loop()
        loop = asyncio.get_event_loop()
        created_tasks.append(
            loop.create_task(
                naughty_task(),
            )
        )
        await asyncio.sleep(0.01)
        raise Exception("Stops the loop")

    with pytest.raises(Exception) as excinfo:
        run(main(), stop_on_unhandled_errors=True, timeout_task_shutdown=2.0)

    print(excinfo.value)
    print(excinfo.traceback)
    assert "tasks refused to exit after 2.0 seconds" in caplog.text
    assert "Stops the loop" in str(excinfo.value)
    assert all(t.cancelled for t in created_tasks)


