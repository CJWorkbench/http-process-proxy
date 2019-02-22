#!/usr/bin/env python3

import asyncio
import logging

import websockets

logger = logging.getLogger(__name__)


async def _echo(websocket, _path: str):
    data = await websocket.recv()
    logger.info("Echoing client data: %r", data)

    await websocket.send(data)


async def serve():
    """
    Websockets server listening on localhost:8011.

    Usage:

        async with server() as ws_server:
            # Within this block, we'll be listening and handling connections.
            await asyncio.sleep(9999)
            # When the block exits, the server goes down.
    """
    await websockets.serve(_echo, "localhost", 8011)
    await asyncio.sleep(99999999)


def main():
    asyncio.run(serve())


if __name__ == "__main__":
    main()
