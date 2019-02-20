Live-reloading HTTP reverse proxy for web development.

# Features

* Transparently proxies the web server under development, sending all HTTP
  requests to it.
* Passes-through the under-development web server's stderr/stdout.
* Watches the current working directory for file modifications with
  [Watchman](https://facebook.github.io/watchman/).
* Reloads after anything is modified (respecting
  [.watchmanconfig](https://facebook.github.io/watchman/docs/config.html))
* Queues HTTP connections until the under-development web server starts
  listening for connections.
* Presents a `503 Service Unavailable` error page with debug info if the
  under-development web server shuts down.
* Supports WebSockets connections to the underlying server.

# Usage

You're building a web server. (For example, a Django app.) Your framework
doesn't include a "development mode" -- or its "development mode" doesn't do
what you want. So you run:


```
http-process-proxy localhost:8000 8001 \
    --exec python ./manage.py runserver --noreload 8001
```

That is:

`http-process-proxy BIND:BINDPORT BACKEND:PORT [OPTIONS ...] --exec BACKENDCOMMAND ...`

Where:

* `BIND:PORT` is the address and port to listen on (e.g., `0.0.0.0:8000`,
  `localhost:9000`, ...)
* `BACKEND:PORT` is the address of the server we're proxying
* `BACKENDCOMMAND ...` is the command to run the web-server we're developing,
  which must listen on `BACKEND:PORT`.

# Gory details

This web server cycles through these states:

1. *Loading*: we're starting the backend and waiting to connect to it
    * Incoming connections will buffer.
    * State changes:
        * If a file is modified, kill the backend and transition to "Killing".
        * If we succeed in connecting to the backend, transition to "Running"
          and respond to all buffered incoming connections.
        * If backend exits, transition to "Error" and respond to all buffered
          incoming connections.
2. *Running*: the web server is alive.
    * Incoming connections will pass through.
    * State changes:
        * If a file is modified, kill the backend and transition to "Killing".
          Drop all live HTTP connections.
        * If the backend exits, transition to "Error". Drop all live HTTP
          connections.
3. *Error*: the web server exited of its own accord.
    * Incoming connections will lead to `503 Service Unavailable` errors.
    * State changes:
        * If a file is modified, transition to "Loading".
          Complete all live HTTP connections.
4. *Killing*: 
    * Incoming connections will buffer.
    * State changes:
        * If a file is modified, do nothing.
        * When the subprocess exits, transition to "Loading".

In any state, if the user hits `Ctrl+C` we'll die immediately.
