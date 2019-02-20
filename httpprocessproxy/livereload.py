import asyncio
import importlib.resources
import json
import logging
import types
from contextlib import asynccontextmanager
from http import HTTPStatus

import websockets

LIVERELOAD_BYTES = importlib.resources.read_binary(__package__, "livereload.js")
PORT = 35729

logger = logging.getLogger(__name__)


def _process_livereload_request(path, headers):
    if path.startswith("/livereload.js"):
        return (
            HTTPStatus.OK,
            {
                "Content-Type": "application/javascript",
                "Content-Length": str(len(LIVERELOAD_BYTES)),
                "Access-Control-Allow-Origin": "*",
            },
            LIVERELOAD_BYTES,
        )
    # otherwise, fallback to Websockets-handling


async def _handle_livereload(websocket, _path: str):
    client_hello_str: str = await websocket.recv()
    client_hello = json.loads(client_hello_str)
    logger.info("LiveReload client HELLO: %r", client_hello)

    await websocket.send(
        json.dumps(
            {
                "command": "hello",
                "protocols": ["http://livereload.com/protocols/official-7"],
                "serverName": "http-process-proxy",
            }
        )
    )
    logger.info("Sent server HELLO")

    async for message in websocket:
        logger.debug("LiveReload message: %r", message)


async def _notify(self):
    tasks = {
        websocket.send(json.dumps({"command": "reload", "path": "/"}))
        for websocket in self.websockets
    }
    if tasks:
        await asyncio.wait(tasks)


@asynccontextmanager
async def serve(bind_host: str):
    """
    Websockets server listening on port 35729.

    Implements http://livereload.com/api/protocol/

    Usage:

        async with livereload.server('localhost') as livereload_server:
            # Within this block, we'll be listening and handling connections.
            await livereload_server.notify()  # notify listeners to refresh
            livereload_server.close()
            # When the block exits, the server goes down.
    """
    server = websockets.serve(
        _handle_livereload, bind_host, PORT, process_request=_process_livereload_request
    )
    async with server as live_server:
        # monkey-patch the server -- easier than yielding a whole new class
        live_server.notify = types.MethodType(_notify, live_server)
        yield live_server
