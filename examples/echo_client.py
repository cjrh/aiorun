import asyncio
from aiorun import run


async def tcp_echo_client(message):
    reader, writer = await asyncio.open_connection(
        '127.0.0.1', 8888)

    print('Send: %r' % message)
    writer.write(message.encode())

    data = await reader.read(100)
    print('Received: %r' % data.decode())

    print('Close the socket')
    writer.close()
    asyncio.get_event_loop().stop()

message = 'Hello World!'
run(tcp_echo_client(message))
