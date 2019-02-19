import asyncio
from dataclasses import dataclass, field
from functools import partial
import logging
import re
import subprocess
import sys
from typing import List, Optional, Set


logger = logging.getLogger(__name__)
FORWARDED_PATTERN = re.compile(rb'(\r\nForwarded:.*?)(\r\n)', re.IGNORECASE)
CONTENT_LENGTH_PATTERN = re.compile(rb'\r\nContent-Length:\s*(\d+)',
                                    re.IGNORECASE)
CHUNKED_PATTERN = re.compile(rb'\r\nTransfer-Encoding:\s+chunked',
                             re.IGNORECASE)


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
        asyncio.streams.IncompleteReadError: connection closed
    """
    content = await reader.readuntil(b'\r\n\r\n')
    return HTTPHeader(content)


async def _pipe_http_body(header: HTTPHeader, reader: asyncio.StreamReader,
                          writer: Optional[asyncio.StreamWriter]) -> None:
    """
    Given HTTP request/response headers, pipe body from `reader` to `writer`.

    Pass `writer=None` to ignore the body.

    Exceptions:
        asyncio.streams.IncompleteReadError: connection closed
    """
    is_chunked = header.is_transfer_encoding_chunked
    content_length = header.content_length
    block_size = 1024 * 50  # stream 50kb at a time, for progressive loading

    if is_chunked:
        raise NotImplementedError
    elif content_length is not None:
        n_remaining = content_length
        while n_remaining > 0 and not reader.at_eof():
            n = min(n_remaining, block_size)
            block = await reader.read(n)

            if block and writer is not None:
                writer.write(block)
                await writer.drain()

            n_remaining -= len(block)
    else:
        # No Content-Length, no Transfer-Encoding: chunked -> there's no body
        # and so we're already done.
        pass


class BackendHTTPProtocol(asyncio.Protocol):
    def __init__(self, frontend_protocol):
        self.frontend_protocol = frontend_protocol

    # override
    def connection_made(self, transport):
        self.transport = transport
        self.frontend_protocol.backend_connection_made(self)

    # override
    def connection_lost(self, exc):
        if exc:
            logger.exception('Backend connection lost')
        else:
            logger.info(
                'Backend connection lost; aborting frontend connection'
            )
        self.frontend_protocol.transport.abort()

    # override
    def data_received(self, data):
        self.frontend_protocol.transport.write(data)

    # override
    def eof_received(self):
        self.frontend_protocol.transport.write_eof()


class FrontendHTTPProtocol(asyncio.Protocol):
    """
    A connection from the client web browser to our proxy-server frontend.

    This may exist even when the backend server is offline. In that case, we
    buffer request data.

    This goes through these states, in order:

        1. `self.http_buffer` buffers request.
        2. `self.backend_connection` is set; `self.http_buffer` is flushed and
           deleted. Data is piped from frontend to backend and vice-versa.
        3. `self.is_connection_lost` is set; everything is closed.

    Step 2 may be skipped.
    """

    def __init__(self):
        super().__init__()
        self.is_connection_lost = False
        self.backend_connection = None

        # Buffer request, while the backend is unavailable. If
        # `self.http_buffer.eof`, the entire request has been received.
        self.http_buffer = HTTPBuffer()

        # Has the backend sent us the start of an HTTP response? (If so, we
        # can't use override_response.)
        self.is_response_sending = False

        self.override_response = None

    def start_connect_to_backend(self, config: BackendConfig) -> None:
        """
        Queue a backend connection to serve this frontend HTTP request.
        """
        asyncio.create_task(self._connect_to_backend(config))

    def abort(self):
        if self.transport:
            self.transport.close()

    async def _connect_to_backend(self, config: BackendConfig) -> None:
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_connection(
            partial(BackendHTTPProtocol, self),
            config.host,
            config.port
        )

    def report_process_exit(self, code: int) -> None:
        """
        Set self.override_response: we'll respond to each request with 503.
        """

        message = b'\n'.join([
            b'Server process exited with code ' + str(returncode).encode('utf-8'),
            b'Read console logs for details.',
            b'Edit code to restart the server.',
        ])

        self.override_response = b'\r\n'.join([
            b'HTTP/1.1 503 Service Unavailable',
            b'Content-Type: text/plain; charset=utf-8',
            b'Content-Length: ' + str(len(message)).encode('utf-8'),
            b'',
            message
        ])

        if self.http_buffer is not None and self.http_buffer.eof:
            self.http_buffer = None
            self.transport.write(self.override_response)
            self.transport.write_eof()

    # override
    def connection_made(self, transport):
        self.transport = transport

    def backend_connection_made(self, backend_connection):
        if self.is_connection_lost:
            backend_connection.transport.close()
        else:
            for block in self.http_buffer.blocks:
                backend_connection.transport.write(block)
            if self.http_buffer.eof:
                backend_connection.transport.write_eof()
            self.backend_connection = backend_connection
            self.http_buffer = None

    # override
    def connection_lost(self, exc):
        if exc:
            logger.exception('Client-to-frontend connection lost', exc)
        else:
            logger.info('Client-to-frontend connection lost')
        self.is_connection_lost = True
        self.http_buffer = None
        if self.backend_connection:
            self.backend_connection.transport.close()
        self.backend_connection = None

    # override
    def data_received(self, data):
        if self.is_connection_lost:
            return
        elif self.backend_connection is None:
            self.http_buffer.blocks.append(data)
        else:
            self.backend_connection.transport.write(data)

    # override
    def eof_received(self):
        if self.override_response is not None:
            self.transport.write(self.override_response)
            self.transport.write_eof()
        elif self.backend_connection is None:
            self.http_buffer.eof = True
        else:
            self.backend_connection.transport.write_eof()


class State:
    def on_reload(self):
        """
        Handle user-initiated request to kill the running process.

        `wait_for_next_state()` should be monitoring something; calling this
        must trigger that monitor.

        It's fine to call this multiple times on a State.
        """

    def on_frontend_connected(self, reader: asyncio.StreamReader, writer:
                              asyncio.StreamWriter):
        """
        Handle user-initiated HTTP connection.
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
        logger.info('Post-init: proxy connection!')
        asyncio.create_task(self._handle())

    async def _handle(self) -> None:
        """
        Proxy the frontend connection to the backend.
        """
        try:
            backend_reader, backend_writer = await (
                asyncio.open_connection(self.config.host, self.config.port)
            )
        except OSError as err:
            logger.exception('Error during connect')
            return  # TODO finish with freader/fwriter

        # Handle requests -- even when keepalive is enabled (which means
        # multiple requests on same connection)
        while (
            not self.frontend_reader.at_eof()
            and not backend_reader.at_eof()
        ):
            await self._handle_one_request(backend_reader, backend_writer)

        # Close both connections
        backend_writer.close()
        await backend_writer.wait_closed()
        self.frontend_writer.close()
        await self.frontend_writer.wait_closed()

    async def _handle_one_request(
        self,
        backend_reader: asyncio.StreamReader,
        backend_writer: asyncio.StreamWriter
    ) -> None:
        # 1. Pipe request from frontend_reader to backend_writer
        try:
            request_header = await _read_http_header(self.frontend_reader)
        except EOFError:
            logger.debug('Connection closed; aborting handler')
            return
        munged_header_bytes = self._munge_header_bytes(request_header.content)
        backend_writer.write(munged_header_bytes)
        await backend_writer.drain()
        await _pipe_http_body(request_header, self.frontend_reader,
                              backend_writer)

        # 2. Pipe response from backend_reader to frontend_writer
        # (An HTTP connection can only write a response after the entire
        # request is transmitted.)
        response_header = await _read_http_header(backend_reader)
        self.frontend_writer.write(response_header.content)
        await self.frontend_writer.drain()
        await _pipe_http_body(response_header, backend_reader,
                              self.frontend_writer)

    def _munge_header_bytes(self, header_bytes: bytes) -> bytes:
        """
        Add or modify `Forwarded` header.
        """
        sockname = self.frontend_writer.get_extra_info('sockname')

        if len(sockname) == 2:
            # AF_INET: (host, port)
            host = ('%s:%d' % sockname)
        elif len(sockname) == 4:
            # AF_INET6: (host, port, flowinfo, scopeid)
            host = ('"[%s]:%d"' % sockname[:2])

        munged_bytes, matched = FORWARDED_PATTERN.subn(
            lambda pre, post: pre + b';for=' + host.encode('ascii') + post,
            header_bytes
        )
        if matched:
            return munged_bytes
        else:
            return header_bytes.replace(
                b'\r\n\r\n',
                b'\r\nForwarded: ' + host.encode('ascii') + b'\r\n\r\n'
            )

    async def _pipe(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ) -> None:
        """
        Transcribe request body from `reader` to `writer`.
        """
        while not reader.at_eof():
            block = reader.read(self.BLOCK_SIZE)

            if block:
                writer.write(block)
                await writer.drain()


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
            return StateKilling(self.config, process, self.connections)
        elif process.returncode is not None:
            for connection in self.connections: # TODO make errors
                connection.report_process_exit(process.returncode)
            return StateError(self.config, process.returncode)
        else:  # we've connected, and `process` is running
            # Make each connection connect to the backend
            [ProxiedConnection(self.config, c.frontend_reader,
                               c.frontend_writer)
             for c in self.connections]
            return StateRunning(
                self.config,
                process
            )

    async def _poll_until_accept_or_die_or_kill(self, process):
        """
        Keep trying to connect to the backend, until success or `self.killed`.

        Either way, return normally.
        """
        died_task = asyncio.create_task(process.wait())
        killed_task = asyncio.create_task(self.killed.wait())

        while not self.killed.is_set() and process.returncode is None:
            logger.debug('Trying to connect')
            connect_task = asyncio.create_task(
                asyncio.open_connection(self.config.host, self.config.port)
            )

            done, pending = await asyncio.wait(
                {connect_task, killed_task, died_task},
                return_when=asyncio.FIRST_COMPLETED
            )

            if connect_task in done:
                # The connection either succeeded or raised.

                try:
                    reader, writer = connect_task.result()  # or raise
                    writer.close()
                    break  # The connection succeeded
                except (
                    asyncio.TimeoutError,
                    OSError,
                    ConnectionRefusedError
                ) as err:
                    # The connection raised -- it didn't succeed
                    logger.debug('Connect poll failed (%s); will retry',
                                 str(err))

            await asyncio.sleep(0.1)
            # and loop

        died_task.cancel()
        killed_task.cancel()


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

        done, pending = await asyncio.wait({killed_task, died_task},
                                           return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if killed_task in done:
            self.process.kill()
            # The connections will all fail on their own
            return StateKilling(self.config, self.process)
        else:
            for connection in self.connections:
                connection.report_process_exit(self.process.returncode)
            return StateError(self.config, self.process.returncode)


@dataclass(frozen=True)
class StateError(State):
    config: BackendConfig
    returncode: int
    reload: asyncio.Event = field(default_factory=asyncio.Event)

    @dataclass(frozen=True)
    class Connection:
        frontend_reader: asyncio.StreamReader
        frontend_writer: asyncio.StreamWriter
        returncode: int

        def __post_init__(self):
            asyncio.create_task(self._handle())

        @property
        def response_bytes(self):
            message = b'\n'.join([
                (
                    b'Server process exited with code '
                    + str(self.returncode).encode('utf-8')
                ),
                b'Read console logs for details.',
                b'Edit code to restart the server.',
            ])

            return b'\r\n'.join([
                b'HTTP/1.1 503 Service Unavailable',
                b'Content-Type: text/plain; charset=utf-8',
                b'Content-Length: ' + str(len(message)).encode('utf-8'),
                b'',
                message
            ])

        async def _handle(self):
            # Read the entire request (we'll ignore it)
            while not self.frontend_reader.at_eof():
                await self._handle_one_request()

            self.frontend_writer.close()
            await self.frontend_writer.wait_closed()

        async def _handle_one_request(self):
            try:
                header = await _read_http_header(self.frontend_reader)
            except EOFError:
                logger.debug('Connection closed; aborting handler')
                return

            # read request bytes, piping them nowhere
            await _pipe_http_body(header, self.frontend_reader, None)

            # respond with our static bytes
            self.frontend_writer.write(self.response_bytes)
            await self.frontend_writer.drain()

    def on_reload(self):
        self.reload.set()

    def on_frontend_connected(self, reader, writer):
        self.Connection(reader, writer, self.returncode)

    async def next_state(self):
        """
        Waits for reload signal, then switches to StateLoading.
        """
        await self.reload.wait()
        return StateLoading(self.config)


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
        return StateLoading(self.config, self.connections)


class Backend:
    def __init__(self, backend_addr: str, backend_command: List[str]):
        backend_host, backend_port = backend_addr.split(':')
        self.backend_host = backend_host
        self.backend_port = int(backend_port)
        self.backend_command = backend_command

    async def run_forever(self):
        config = BackendConfig(self.backend_command, self.backend_host,
                               self.backend_port)
        # Start with state=error because we expect to receive a `.reload()`
        # nearly immediately.
        self.state = StateError(config, 0)
        while True:
            self.state = await self.state.next_state()
            logger.info('Reached state %r', type(self.state))

    def reload(self):
        logger.info('Reloading')
        self.state.on_reload()

    def on_frontend_connected(self, reader: asyncio.StreamReader,
                              writer: asyncio.StreamWriter) -> None:
        self.state.on_frontend_connected(reader, writer)
