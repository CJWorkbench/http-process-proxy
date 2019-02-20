import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Callable, List

import pywatchman

logger = logging.getLogger(__name__)


def _patterns_to_terms(pats):
    # convert a list of globs into the equivalent watchman expression term
    # copy/paste from
    # https://github.com/facebook/watchman/blob/master/python/bin/watchman-make
    if pats is None or len(pats) == 0:
        return ["true"]
    terms = ["anyof"]
    for p in pats:
        terms.append(["match", p, "wholename", {"includedotfiles": True}])
    return terms


@dataclass(frozen=True)
class Watcher:
    """
    Watches a directory and calls `callback` when files change.
    """

    watch_path: str
    watch_patterns: List[str]  # empty means '**/*'
    callback: Callable

    def _emit_notifications(self, loop):
        watchman_client = pywatchman.client()
        logger.debug("Connected to Watchman")

        watch = watchman_client.query("watch-project", self.watch_path)
        if "warning" in watch:
            logger.warning(watch["warning"])
        logger.debug("Watching project: %r", watch)

        query = {
            "expression": _patterns_to_terms(self.watch_patterns),
            "fields": ["name"],
        }

        watchman_client.query("subscribe", watch["watch"], "watchman_sub", query)

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
