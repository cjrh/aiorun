import logging
import asyncio
from aiorun import run


logging.basicConfig(level="DEBUG")


async def main():
    logging.info("Sleeping in main")
    try:
        await asyncio.sleep(50)
    finally:
        logging.info("Leaving main")


run(main())
logging.critical("Leaving fake main")
