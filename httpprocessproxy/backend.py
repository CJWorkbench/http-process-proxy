import asyncio
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set

logger = logging.getLogger(__name__)
FORWARDED_PATTERN = re.compile(rb"(\r\nForwarded:.*?)(\r\n)", re.IGNORECASE)
CONTENT_LENGTH_PATTERN = re.compile(rb"\r\nContent-Length:\s*(\d+)", re.IGNORECASE)
CHUNKED_PATTERN = re.compile(rb"\r\nTransfer-Encoding:\s+chunked", re.IGNORECASE)


@dataclass(frozen=True)
class BackendConfig:
    command: List[str]
    host: str
    port: int


@dataclass(frozen=True)
class HTTPBuffer:
    blocks: List[bytes] = field(default_factory=list)
    eof: bool = False


@dataclass(frozen=True)
class HTTPHeader:
    """
    An HTTP Request header or response header.

    For our purposes, requests and responses are treated the same way.
    """

    content: bytes
    """Raw bytes of header data."""

    @property
    def is_transfer_encoding_chunked(self) -> bool:
        """
        True iff the request has Transfer-Encoding: chunked.
        """
        CHUNKED_PATTERN.search(self.content) is not None

    @property
    def content_length(self) -> Optional[int]:
        """
        Number of bytes of data, or `None` if not specified.

        https://www.w3.org/Protocols/rfc2616/rfc2616-sec4.html#sec4.3 specifies
        either Transfer-Encoding or Content-Length must be set: if neither is
        set, there is no body.
        """
        match = CONTENT_LENGTH_PATTERN.search(self.content)
        if match is None:
            return None
        else:
            return int(match.group(1))


async def _read_http_header(reader: asyncio.StreamReader) -> HTTPHeader:
    """
    Read an HTTP header.

    Exceptions:
        EOFError: connection closed before supplying all the header bytes.
    """
    content = await reader.readuntil(b"\r\n\r\n")
    return HTTPHeader(content)


async def _pipe_bytes(
    reader: asyncio.StreamReader,
    writer: Optional[asyncio.StreamWriter],
    n_bytes: Optional[int] = None,
) -> None:
    """
    Pipe bytes from `reader` to `writer`.

    If `n_bytes` is supplied, stop after exactly that many bytes have been
    copied.

    Exceptions:
        EOFError: read failed
        ConnectionError: write failed
        EOFError: `n_bytes` is more bytes than `reader` can provide
    """
    block_size = 1024 * 50  # stream 50kb at a time, for progressive loading
    n_remaining = n_bytes

    while (n_remaining is None or n_remaining > 0) and not reader.at_eof():
        if n_remaining is None:
            n = block_size
        else:
            n = min(n_remaining, block_size)

        block = await reader.read(n)  # raises EOFError
        if block and writer is not None:
            writer.write(block)
            await writer.drain()  # raises ConnectionError

        if n_remaining is not None:
            n_remaining -= len(block)

    if n_remaining is not None and n_remaining > 0:
        raise EOFError(
            "Reader only supplied %d bytes (expected %d)"
            % (n_bytes - n_remaining, n_bytes)
        )


async def _pipe_http_body(
    header: HTTPHeader,
    reader: asyncio.StreamReader,
    writer: Optional[asyncio.StreamWriter],
) -> None:
    """
    Given HTTP request/response headers, pipe body from `reader` to `writer`.

    Pass `writer=None` to ignore the body.

    Exceptions:
        EOFError: read failed
        ConnectionError: write failed
        EOFError: header says body is N bytes; fewer bytes were piped
    """
    is_chunked = header.is_transfer_encoding_chunked
    content_length = header.content_length

    if is_chunked:
        raise NotImplementedError
    elif content_length is not None:
        await _pipe_bytes(reader, writer, content_length)
    else:
        # No Content-Length, no Transfer-Encoding: chunked -> there's no body
        # and so we're already done.
        pass


class State:
    def on_reload(self):
        """
        Handle user-initiated request to kill the running process.

        `wait_for_next_state()` should be monitoring something; calling this
        must trigger that monitor.

        It's fine to call this multiple times on a State.
        """

    def on_frontend_connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """
        Handle user-initiated HTTP connection.
        """

    async def next_state(self):
        """
        Run state-specific logic until the state is done.

        Return (want_change_notify, next_state). want_change_notify means
        clients should refresh, as the next response's output may change.
        """


@dataclass(frozen=True)
class WaitingConnection:
    """
    A connection that's "stalled" -- we neither read nor write.

    We'll un-stall the connection later.
    """

    frontend_reader: asyncio.StreamReader
    frontend_writer: asyncio.StreamWriter


@dataclass(frozen=True)
class ProxiedConnection:
    """
    A live connection from the frontend, being handled by the backend.
    """

    config: BackendConfig
    frontend_reader: asyncio.StreamReader
    frontend_writer: asyncio.StreamWriter

    BLOCK_SIZE = 1024 * 64  # 64kb -- pretty small, for progress reporting

    def __post_init__(self):
        logger.info("Post-init: proxy connection!")
        asyncio.create_task(self._handle())

    async def _handle(self) -> None:
        """
        Proxy the frontend connection to the backend.

        A single HTTP connection can have multiple HTTP _requests_. We support
        HTTP/1.1 keepalive (meaning many requests happen in serial, with
        "Forwarded" header mangling) and HTTP/1.1 UPGRADE (Websockets --
        meaning bidirectional traffic until one side closes the connection).

        At some point, one of four things will happen:

            * Reading request from browser gives EOFError (browser left)
            * Writing request to backend gives ConnectionError (backend died)
            * Reading response from backend gives EOFError (backend died)
            * Writing response to browser gives ConnectionError (browser left)

        No matter what the case, we'll clean up by closing both connections.
        """
        try:
            backend_reader, backend_writer = await (
                asyncio.open_connection(self.config.host, self.config.port)
            )
        except OSError:
            logger.exception("Error during connect")
            await self._close(None)
            return

        # Handle requests -- even when keepalive is enabled (which means
        # multiple requests on same connection)
        while not self.frontend_reader.at_eof() and not backend_reader.at_eof():
            try:
                await self._handle_one_request(backend_reader, backend_writer)
            except (EOFError, ConnectionError):
                break

        # Close both connections
        await self._close(backend_writer)

    async def _handle_one_request(
        self, backend_reader: asyncio.StreamReader, backend_writer: asyncio.StreamWriter
    ) -> None:
        """
        Respond to one HTTP request, keeping the connection alive.

        Raises EOFError or ConnectionError when the browser or backend
        disconnects.
        """

        # 1. Pipe request from frontend_reader to backend_writer
        # raises EOFError if browser disconnects
        request_header = await _read_http_header(self.frontend_reader)
        munged_header_bytes = self._munge_header_bytes(request_header.content)
        backend_writer.write(munged_header_bytes)
        # raises ConnectionError if backend disconnects
        await backend_writer.drain()
        # raises EOFError if browser disconnects
        # raises ConnectionError if backend disconnects
        await _pipe_http_body(request_header, self.frontend_reader, backend_writer)

        # 2. Pipe response from backend_reader to frontend_writer
        # (An HTTP connection can only write a response after the entire
        # request is transmitted.)
        # raises EOFError if backend disconnects
        response_header = await _read_http_header(backend_reader)
        self.frontend_writer.write(response_header.content)
        # raises ConnectionError if browser disconnects
        await self.frontend_writer.drain()
        # raises EOFError if backend disconnects
        # raises ConnectionError if browser disconnects
        await _pipe_http_body(response_header, backend_reader, self.frontend_writer)

        if response_header.content.startswith(b"HTTP/1.1 101"):
            # HTTP 101 Switching Protocol: this is no longer an HTTP/1.1
            # connection, so the HTTP/1.1 rules don't apply. It's probably
            # Websockets, which has bidirectional traffic. Pipe everything
            # simultaneously.

            to_backend = asyncio.create_task(
                _pipe_bytes(self.frontend_reader, backend_writer)
            )
            to_browser = asyncio.create_task(
                _pipe_bytes(backend_reader, self.frontend_writer)
            )
            # When (not "if") either pipe raises EOFError or ConnectionError,
            # cancel the tasks and return. The caller will clean up.
            #
            # There is no way for this task to end other than closing the
            # connection. That's just fine. Either the web browser closes the
            # connection (meaning we should disconnect from the backend), or
            # the backend closes the connection (meaning we should disconnect
            # from the browser -- and let the browser reconnect when it makes
            # its next request).
            try:
                await asyncio.wait(
                    {to_backend, to_browser}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                # Close everything
                to_backend.cancel()
                to_browser.cancel()
                # and now return. The caller will close the connections.

    async def _close(self, backend_writer: Optional[asyncio.StreamWriter]) -> None:
        logger.info("Connection closed; cleaning up")
        self.frontend_writer.close()
        tasks = {self.frontend_writer.wait_closed()}

        if backend_writer is not None:
            backend_writer.close()
            tasks.add(backend_writer.wait_closed())

        await asyncio.wait(tasks)

    def _munge_header_bytes(self, header_bytes: bytes) -> bytes:
        """
        Add or modify `Forwarded` header.
        """
        sockname = self.frontend_writer.get_extra_info("sockname")

        if len(sockname) == 2:
            # AF_INET: (host, port)
            host = "%s:%d" % sockname
        elif len(sockname) == 4:
            # AF_INET6: (host, port, flowinfo, scopeid)
            host = '"[%s]:%d"' % sockname[:2]

        munged_bytes, matched = FORWARDED_PATTERN.subn(
            lambda pre, post: pre + b";for=" + host.encode("ascii") + post, header_bytes
        )
        if matched:
            return munged_bytes
        else:
            return header_bytes.replace(
                b"\r\n\r\n", b"\r\nForwarded: " + host.encode("ascii") + b"\r\n\r\n"
            )


@dataclass(frozen=True)
class ErrorConnection:
    frontend_reader: asyncio.StreamReader
    frontend_writer: asyncio.StreamWriter
    returncode: int
    reload: asyncio.Event
    """
    When set, disconnect everything.
    """

    def __post_init__(self):
        asyncio.create_task(self._handle())

    @property
    def response_bytes(self) -> bytes:
        message = b"\n".join(
            [
                (
                    b"Server process exited with code "
                    + str(self.returncode).encode("utf-8")
                ),
                b"Read console logs for details.",
                b"Edit code to restart the server.",
            ]
        )

        return b"\r\n".join(
            [
                b"HTTP/1.1 503 Service Unavailable",
                b"Content-Type: text/plain; charset=utf-8",
                b"Content-Length: " + str(len(message)).encode("utf-8"),
                b"",
                message,
            ]
        )

    async def _handle(self):
        """
        Respond to HTTP requests until browser disconnect or reload.

        Browsers tend to connect with HTTP Keepalive. Upon reload we want to
        kill the connection, so the browser reconnects under the next state.
        """

        reload_task = asyncio.create_task(self.reload.wait())
        request_task = None

        while not self.frontend_reader.at_eof() and not self.reload.is_set():
            request_task = asyncio.create_task(self._handle_one_request())
            done, pending = await asyncio.wait(
                {request_task, reload_task}, return_when=asyncio.FIRST_COMPLETED
            )
            # if request is done, loop
            # if reload is done, loop -- we test self.reload.is_set()
            # if both are done, that's fine

        self.frontend_writer.close()
        await self.frontend_writer.wait_closed()

        if reload_task in pending:
            reload_task.cancel()

        if request_task in pending:
            await request_task  # it should finish soon: its transport closed

    async def _handle_one_request(self):
        """
        Read any HTTP request and respond with 503 Service Unavailable.

        Return if the browser disconnects.
        """
        try:
            # Read the entire request (we'll ignore it)
            # raises EOFError
            header = await _read_http_header(self.frontend_reader)

            # read request bytes, piping them nowhere
            # raises EOFError
            await _pipe_http_body(header, self.frontend_reader, None)

            # respond with our static bytes
            self.frontend_writer.write(self.response_bytes)
            # raises ConnectionError
            await self.frontend_writer.drain()
        except (EOFError, ConnectionError):
            logger.debug("Connection closed; aborting handler")


@dataclass(frozen=True)
class StateLoading(State):
    config: BackendConfig
    connections: Set[WaitingConnection] = field(default_factory=set)

    killed: asyncio.Event = field(default_factory=asyncio.Event)
    """
    Event set when we want to transition to killing.

    (This exists because we'll often kill _while_ we're spawning a process or
    polling for it to start accepting connections.)
    """

    def on_reload(self):
        self.killed.set()

    def on_frontend_connected(self, reader, writer):
        self.connections.add(WaitingConnection(reader, writer))

    async def next_state(self):
        """
        Launch the process and wait for one of the following transitions:

            * `self.killed` being set => switch to StateKilling.
            * process accepts a connection => switch to StateRunning.
            * process exits => switch to StateError.
        """
        process = await asyncio.create_subprocess_exec(
            *self.config.command,
            stdin=subprocess.DEVNULL,
            stdout=sys.stdout,
            stderr=sys.stderr
        )

        await self._poll_until_accept_or_die_or_kill(process)

        if self.killed.is_set():
            process.kill()
            return (False, StateKilling(self.config, process, self.connections))
        elif process.returncode is not None:
            # Transform each connection: it should report errors now
            state = StateError(self.config, process.returncode)
            for connection in self.connections:
                ErrorConnection(
                    connection.frontend_reader,
                    connection.frontend_writer,
                    process.returncode,
                    state.reload,
                )
            return (False, state)
        else:  # we've connected, and `process` is running
            # Make each connection connect to the backend
            [
                ProxiedConnection(self.config, c.frontend_reader, c.frontend_writer)
                for c in self.connections
            ]
            return (False, StateRunning(self.config, process))

    async def _poll_until_accept_or_die_or_kill(self, process):
        """
        Keep trying to connect to the backend, until success or `self.killed`.

        Either way, return normally.
        """
        died_task = asyncio.create_task(process.wait())
        killed_task = asyncio.create_task(self.killed.wait())

        while not self.killed.is_set() and process.returncode is None:
            logger.debug("Trying to connect")

            poll_task = asyncio.create_task(self._poll_once())

            done, pending = await asyncio.wait(
                {poll_task, killed_task, died_task}, return_when=asyncio.FIRST_COMPLETED
            )

            if poll_task in done:
                # The connection either succeeded or raised.

                try:
                    poll_task.result()  # or raise
                    break  # The connection succeeded
                except (
                    asyncio.TimeoutError,
                    OSError,
                    ConnectionRefusedError,
                    ConnectionResetError,
                ) as err:
                    # The connection raised -- it didn't succeed
                    logger.debug("Connect poll failed (%s); will retry", str(err))
            else:
                poll_task.cancel()

            await asyncio.sleep(0.1)
            # and loop

        died_task.cancel()
        killed_task.cancel()

    async def _poll_once(self):
        """
        Return if we can make an HTTP request (regardless of response).

        Raise otherwise.
        """
        reader, writer = await asyncio.open_connection(
            self.config.host, self.config.port
        )
        writer.write(b"HEAD / HTTP/1.1\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await reader.readline()


@dataclass(frozen=True)
class StateRunning(State):
    config: BackendConfig
    process: asyncio.subprocess.Process

    killed: asyncio.Event = field(default_factory=asyncio.Event)
    """
    Event set when we want to transition to killing.
    """

    def on_reload(self):
        self.killed.set()

    def on_frontend_connected(self, reader, writer):
        ProxiedConnection(self.config, reader, writer)

    async def next_state(self):
        """
        Keep accepting connections, until self.process dies.
        """
        killed_task = asyncio.create_task(self.killed.wait())
        died_task = asyncio.create_task(self.process.wait())

        done, pending = await asyncio.wait(
            {killed_task, died_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if killed_task in done:
            self.process.kill()
            # The connections will all fail on their own after the process dies
            return (True, StateKilling(self.config, self.process))
        else:
            # The connections will all fail on their own
            return (True, StateError(self.config, self.process.returncode))


@dataclass(frozen=True)
class StateError(State):
    config: BackendConfig
    returncode: int
    reload: asyncio.Event = field(default_factory=asyncio.Event)

    def on_reload(self):
        self.reload.set()

    def on_frontend_connected(self, reader, writer):
        ErrorConnection(reader, writer, self.returncode, self.reload)

    async def next_state(self):
        """
        Waits for reload signal, then switches to StateLoading.

        Clients should refresh after this switch, because the response may
        change.
        """
        await self.reload.wait()
        return (True, StateLoading(self.config))


@dataclass(frozen=True)
class StateKilling(State):
    config: BackendConfig
    process: asyncio.subprocess.Process
    connections: Set[WaitingConnection] = field(default_factory=set)

    def on_frontend_connected(self, reader, writer):
        self.connections.add(WaitingConnection(reader, writer))

    def on_reload(self):
        pass  # we're already reloading

    async def next_state(self):
        """
        Waits for kill to complete, then switches to StateLoading.
        """
        await self.process.wait()
        return (False, StateLoading(self.config, self.connections))


class Backend:
    def __init__(
        self,
        backend_addr: str,
        backend_command: List[str],
        notify_backend_change: Callable,
    ):
        backend_host, backend_port = backend_addr.split(":")
        self.config = BackendConfig(backend_command, backend_host, backend_port)
        self.notify_backend_change = notify_backend_change

    async def run_forever(self):
        self.state = StateLoading(self.config)
        while True:
            want_notify, self.state = await self.state.next_state()
            logger.info("Reached state %r", type(self.state))
            if want_notify:
                logger.info("Notifying of state change")
                await self.notify_backend_change()

    def reload(self):
        logger.info("Reloading")
        self.state.on_reload()

    def on_frontend_connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        logger.debug("Connect with state %r", type(self.state))
        self.state.on_frontend_connected(reader, writer)
