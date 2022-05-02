import asyncio
from aiorun import run, shutdown_waits_for

async def corofn():
    for i in range(10):
        print(i)
        await asyncio.sleep(1)
    print('done!')

async def main():
    try:
        await shutdown_waits_for(corofn())
    except asyncio.CancelledError:
        print('oh noes!')

run(main())
