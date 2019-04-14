import asyncio
import logging

from aiorun import run

logging.basicConfig(level="DEBUG")


@run(executor_workers=20)
async def main():
    logging.info("Sleeping in main")
    try:
        await asyncio.sleep(50)
    finally:
        logging.info("Leaving main")
