import asyncio
from aiorun import run


async def job():
    raise Exception("ouch")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(job())

    run(loop=loop, stop_on_unhandled_errors=True)
