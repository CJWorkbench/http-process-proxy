import asyncio
import logging
import threading

import pywatchman

logger = logging.getLogger(__name__)


class Watcher:
    """
    Watches a directory and calls `callback` when files change.
    """

    def __init__(self, watch_path, callback):
        self.watch_path = watch_path
        self.callback = callback

    def _emit_notifications(self, loop):
        watchman_client = pywatchman.client()
        logger.debug("Connected to Watchman")

        watch = watchman_client.query("watch-project", self.watch_path)
        if "warning" in watch:
            logger.warning(watch["warning"])
        logger.debug("Watching project: %r", watch)

        watchman_client.query("subscribe", watch["watch"], "watchman_sub", {})

        watchman_client.setTimeout(None)

        while True:
            watchman_client.receive()
            logger.debug("Something changed")

            data = watchman_client.getSubscription("watchman_sub")

            if data:
                logger.debug("Notifying")
                loop.call_soon_threadsafe(self.callback)

    def watch_forever_in_background(self):
        # TODO switch to aio when pywatchman supports it
        loop = asyncio.get_running_loop()
        thread = threading.Thread(
            target=self._emit_notifications, args=(loop,), daemon=True
        )
        thread.start()
