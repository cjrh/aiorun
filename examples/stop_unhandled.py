from aiorun import run


async def main():
    raise Exception("ouch")


if __name__ == "__main__":
    run(main(), stop_on_unhandled_errors=True)
