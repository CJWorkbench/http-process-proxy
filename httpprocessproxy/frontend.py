import asyncio
import base64
from dataclasses import dataclass
import hashlib
from http import HTTPStatus
import json
import logging
from pathlib import Path
import re
from typing import List
import websockets
from websockets.framing import Frame
from .backend import Backend
from .watcher import Watcher


#logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
LIVERELOAD_BYTES = (Path(__file__).parent / 'livereload.js').read_bytes()


# override
def _process_livereload_request(path, headers):
    if path.startswith('/livereload.js'):
        return (
            HTTPStatus.OK,
            {
                'Content-Type': 'application/javascript',
                'Content-Length': str(len(LIVERELOAD_BYTES)),
                'Access-Control-Allow-Origin': '*',
            },
            LIVERELOAD_BYTES
        )
    # otherwise, fallback to Websockets-handling


async def _handle_livereload(websocket, _path: str):
    client_hello_str: str = await websocket.recv()
    client_hello = json.loads(client_hello_str)
    logger.info('LiveReload client HELLO: %r', client_hello)

    await websocket.send(json.dumps({
        'command': 'hello',
        'protocols': ['http://livereload.com/protocols/official-7'],
        'serverName': 'http-process-proxy',
    }))
    logger.info('Sent server HELLO')

    async for message in websocket:
        logger.debug('LiveReload message: %r', message)


class Frontend:
    def __init__(self, watch_path: str, bind_addr: str, backend_addr: str,
                 backend_command: List[str]):
        self.watch_path = watch_path
        self.bind_addr = bind_addr
        self.backend_addr = backend_addr
        self.backend_command = backend_command

    async def serve_forever(self):
        bind_host, bind_port = self.bind_addr.split(':')

        livereload_server = websockets.serve(
            _handle_livereload,
            bind_host,
            35729,
            process_request=_process_livereload_request
        )

        async with livereload_server as livereload_ws_server:
            backend = Backend(self.backend_addr, self.backend_command)

            def reload():
                backend.reload()
                for websocket in livereload_ws_server.websockets:
                    asyncio.create_task(websocket.send(json.dumps({
                        'command': 'reload',
                        'path': '/',
                    })))

            server = await asyncio.start_server(backend.on_frontend_connected,
                                                bind_host, bind_port)

            watcher = Watcher(self.watch_path, reload)
            watcher.watch_forever_in_background()

            done, pending = await asyncio.wait(
                {
                    backend.run_forever(),
                    server.serve_forever(),
                    #watcher.watch_forever(),
                },
                return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
