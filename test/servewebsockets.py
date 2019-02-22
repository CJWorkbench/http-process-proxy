#!/usr/bin/env python3

import asyncio
import logging

import websockets


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _echo(websocket, _path: str):
    try:
        async for message in websocket:
            logger.info("Echoing client message: %r", message)
            await websocket.send(message)
    except websockets.exceptions.ConnectionClosed:
        logger.info('Client disconnected')
        # return


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
