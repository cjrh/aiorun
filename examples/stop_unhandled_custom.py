import asyncio
from aiorun import run


async def job():
    raise Exception("ouch")


async def other_job():
    try:
        await asyncio.sleep(10)
    except asyncio.CancelledError:
        print("other_job was cancelled!")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(job())
    loop.create_task(other_job())

    def handler(loop, context):
        # https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.call_exception_handler
        print(f'Stopping loop due to error: {context["exception"]} ')
        loop.stop()

    loop.set_exception_handler(handler=handler)

    run(loop=loop)
