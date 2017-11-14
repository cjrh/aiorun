import asyncio
from aiorun import run


async def handle_echo(reader, writer):
    data = await reader.read(100)
    message = data.decode()
    addr = writer.get_extra_info('peername')
    print("Received %r from %r" % (message, addr))

    print("Send: %r" % message)
    writer.write(data)
    await writer.drain()

    print("Close the client socket")
    writer.close()


async def main():
    server = await asyncio.start_server(handle_echo, '127.0.0.1', 8888)
    print('Serving on {}'.format(server.sockets[0].getsockname()))
    try:
        while True:
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        server.close()
        await server.wait_closed()

run(main())
