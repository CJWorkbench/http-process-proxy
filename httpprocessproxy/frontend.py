import asyncio
import logging
from typing import List

from . import livereload
from .backend import Backend
from .watcher import Watcher

# logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class Frontend:
    def __init__(
        self,
        watch_path: str,
        bind_addr: str,
        backend_addr: str,
        backend_command: List[str],
    ):
        self.watch_path = watch_path
        self.bind_addr = bind_addr
        self.backend_addr = backend_addr
        self.backend_command = backend_command

    async def serve_forever(self):
        bind_host, bind_port = self.bind_addr.split(":")

        async with livereload.serve(bind_host) as livereload_server:
            backend = Backend(
                self.backend_addr, self.backend_command, livereload_server.notify
            )

            def reload():
                backend.reload()

            server = await asyncio.start_server(
                backend.on_frontend_connected, bind_host, bind_port
            )

            watcher = Watcher(self.watch_path, reload)
            watcher.watch_forever_in_background()

            done, pending = await asyncio.wait(
                {
                    backend.run_forever(),
                    server.serve_forever(),
                    # watcher.watch_forever(),
                },
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
