v0.0.10 (2019-09-25)
~~~~~~~~~~~~~~~~~~~~

* Forward "Transfer-Encoding: chunked" bodies

v0.0.9 (2019-02-26)
~~~~~~~~~~~~~~~~~~~

* Fix for previous setTimeout error

v0.0.8 (2019-02-26)
~~~~~~~~~~~~~~~~~~~

* Disable timeout before querying "watch-project". Should make startup
  more reliable. (We got a timeout on a 2015 2-core OS X machine.)

v0.0.7 (2019-02-22)
~~~~~~~~~~~~~~~~~~~

* Avoid race that closed Websockets connections prematurely.

v0.0.6 (2019-02-22)
~~~~~~~~~~~~~~~~~~~

* Close Keepalive connections when changing state

v0.0.5 (2019-02-22)
~~~~~~~~~~~~~~~~~~~

* Add ``--debug`` command-line option. (It's rather verbose!)

v0.0.4 (2019-02-21)
~~~~~~~~~~~~~~~~~~~

* Don't drop connections when transitioning from Waiting to Error

v0.0.3 (2019-02-20)
~~~~~~~~~~~~~~~~~~~

* Disable debug logging.

v0.0.2 (2019-02-20)
~~~~~~~~~~~~~~~~~~~

* Add ``--pattern`` and ``--exclude`` options to filter files.

v0.0.1 (2019-02-20)
~~~~~~~~~~~~~~~~~~~

* Initial release
