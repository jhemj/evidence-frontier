"""Test-only fixed TCP ingress; the actual forensic worker remains internal-only.

For Docker Desktop hosts that cannot publish ports on internal networks.
Worker authentication is end-to-end; this relay has no evidence/analysis mounts.
"""
import asyncio
import os


async def relay(reader, writer):
    upstream = None
    try:
        incoming, upstream = await asyncio.wait_for(asyncio.open_connection(
            os.environ['TEST_WORKER_HOST'], 8766), timeout=10)

        async def copy(source, destination):
            while data := await asyncio.wait_for(source.read(65536), timeout=900):
                destination.write(data)
                await destination.drain()
            if destination.can_write_eof():
                destination.write_eof()

        await asyncio.gather(copy(reader, upstream), copy(incoming, writer))
    except (OSError, TimeoutError, ConnectionError):
        pass
    finally:
        for stream in (upstream, writer):
            if stream is not None:
                stream.close()


async def main():
    server = await asyncio.start_server(relay, '0.0.0.0', 8776, limit=65536)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
