import asyncio
import os
import time
import threading
from signal import SIGINT, SIGTERM
from concurrent.futures import ThreadPoolExecutor
from aiorun import run


def kill(sig=SIGTERM, after=0.1):
    """Tool to send a signal after a pause"""
    def job():
        pid = os.getpid()
        time.sleep(after)
        os.kill(pid, sig)

    t = threading.Thread(target=job)
    t.start()


def test_sigterm():
    """Basic SIGTERM"""
    async def main():
        await asyncio.sleep(5.0)

    kill(SIGTERM)
    run(main())


def test_no_coroutine():
    """Signal should still work without a main coroutine"""
    kill(SIGTERM)
    run()


def test_sigint():
    """Basic SIGINT"""
    kill(SIGINT)
    run()


def test_exe():
    """Custom executor"""
    exe = ThreadPoolExecutor()
    kill(SIGTERM)
    run(executor=exe)


def test_sigint_pause():
    """Trying to send multiple signals."""
    async def main():
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)

    kill(SIGINT, after=0.1)
    kill(SIGINT, after=0.15)
    run(main())
