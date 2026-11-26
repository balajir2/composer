"""A TCP proxy in front of the test database that can go silent mid-conversation.

It models a connection Neon (or the network) blackholes: the TCP connection stays open, bytes
the client sends are accepted and dropped, and nothing ever comes back. No RST, no FIN, so only
a client-side bound can end the wait.

``trigger`` picks when a connection goes silent: ``None`` forwards nothing from the first byte
(a fully blackholed database); a byte string forwards normally until the client sends a packet
containing it (for example a table name in a query), then that connection goes silent for good.
Every connection opened after the trigger fired is silent from the start, like a database that
stopped answering.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Coroutine
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class BlackholeProxy:
    def __init__(self, upstream_host: str, upstream_port: int, trigger: bytes | None) -> None:
        self._upstream = (upstream_host, upstream_port)
        self._trigger = trigger
        self.fired = trigger is None
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in self._tasks:
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(BaseException):
                await task
        for writer in self._writers:
            writer.close()

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        if self.fired:
            await self._swallow(reader)
            return
        up_reader, up_writer = await asyncio.open_connection(*self._upstream)
        self._writers.add(up_writer)
        silent = asyncio.Event()
        self._spawn(self._client_to_server(reader, up_writer, silent))
        self._spawn(self._server_to_client(up_reader, writer, silent))

    async def _swallow(self, reader: asyncio.StreamReader) -> None:
        while await reader.read(65536):
            pass

    async def _client_to_server(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, silent: asyncio.Event
    ) -> None:
        while data := await reader.read(65536):
            if silent.is_set():
                continue
            if self._trigger is not None and self._trigger in data:
                self.fired = True
                silent.set()
                continue
            writer.write(data)
            await writer.drain()

    async def _server_to_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, silent: asyncio.Event
    ) -> None:
        while data := await reader.read(65536):
            if silent.is_set():
                continue
            writer.write(data)
            await writer.drain()


def via_proxy(database_url: str, port: int) -> str:
    """``database_url`` pointed at the proxy, TLS off (the proxy matches plaintext bytes)."""
    parts = urlsplit(database_url)
    netloc = parts.netloc.rsplit("@", 1)
    host = f"127.0.0.1:{port}"
    new_netloc = f"{netloc[0]}@{host}" if len(netloc) == 2 else host
    query = "&".join(q for q in (parts.query, "sslmode=disable") if q)
    return urlunsplit((parts.scheme, new_netloc, parts.path, query, parts.fragment))


@contextlib.asynccontextmanager
async def blackhole_proxy(
    database_url: str, trigger: bytes | None
) -> AsyncGenerator[BlackholeProxy]:
    parts = urlsplit(database_url)
    proxy = BlackholeProxy(parts.hostname or "localhost", parts.port or 5432, trigger)
    await proxy.start()
    try:
        yield proxy
    finally:
        await proxy.stop()
