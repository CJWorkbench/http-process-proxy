import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Callable, List

import pywatchman

logger = logging.getLogger(__name__)
MatchOptions = {"includedotfiles": True}


def _patterns_to_expression(include, exclude):
    # convert a list of globs into the equivalent watchman expression term
    # copy/paste from
    # https://github.com/facebook/watchman/blob/master/python/bin/watchman-make
    include_any = ["anyof", *[["match", p, "wholename", MatchOptions] for p in include]]
    exclude_any = ["anyof", *[["match", p, "wholename", MatchOptions] for p in exclude]]

    if include:
        if exclude:
            return ["allof", ["not", exclude_any], include_any]
        else:
            return include_any
    else:
        if exclude:
            return ["not", exclude_any]
        else:
            return ["true"]


@dataclass(frozen=True)
class Watcher:
    """
    Watches a directory and calls `callback` when files change.
    """

    watch_path: str
    watch_patterns: List[str]  # empty means '**/*'
    watch_exclude_patterns: List[str]  # empty means '**/*'
    callback: Callable

    def _emit_notifications(self, loop):
        watchman_client = pywatchman.client()
        logger.debug("Connected to Watchman")

        watch = watchman_client.query("watch-project", self.watch_path)
        if "warning" in watch:
            logger.warning(watch["warning"])
        logger.debug("Watching project: %r", watch)

        query = {
            "expression": _patterns_to_expression(
                self.watch_patterns, self.watch_exclude_patterns
            ),
            "fields": ["name"],
        }
        logger.debug("Watch query: %r", query)

        watchman_client.query("subscribe", watch["watch"], "watchman_sub", query)
        watchman_client.setTimeout(None)

        while True:
            result = watchman_client.receive()  # wait for message from watchman
            watchman_client.getSubscription("watchman_sub")  # nix server cache
            logger.debug("Notifying because files changed: %r", result["files"])
            loop.call_soon_threadsafe(self.callback)

    def watch_forever_in_background(self):
        # TODO switch to aio when pywatchman supports it
        loop = asyncio.get_running_loop()
        thread = threading.Thread(
            target=self._emit_notifications, args=(loop,), daemon=True
        )
        thread.start()
