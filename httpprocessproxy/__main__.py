#!/usr/bin/env python3

import argparse
import asyncio
import os

from .frontend import Frontend


def main():
    parser = argparse.ArgumentParser(
        description="Run the given HTTP server and reload it when files change"
    )
    parser.add_argument(
        "bind_addr", metavar="BIND:ADDR", type=str, help="ADDRESS:PORT to listen on"
    )
    parser.add_argument(
        "backend_addr",
        metavar="BACKEND:PORT",
        type=str,
        help="ADDRESS:PORT of backend server",
    )
    parser.add_argument(
        "-p",
        "--pattern",
        type=str,
        nargs="+",
        help="pattern(s) to watch, e.g. 'src/**/*.html' (default '**/*')",
    )
    parser.add_argument(
        "-x",
        "--exclude",
        type=str,
        nargs="+",
        help="pattern(s) to NOT watch, e.g. 'src/**/test_*' (overrides -p)",
    )
    parser.add_argument(
        "--exec",
        required=True,
        metavar="BACKENDCOMMAND",
        dest="backend_command",
        nargs=argparse.REMAINDER,
        help="Backend server command (must listen at BACKEND:PORT)",
    )

    args = parser.parse_args()

    frontend = Frontend(
        args.bind_addr,
        args.backend_command,
        args.backend_addr,
        os.getcwd(),
        args.pattern or [],
        args.exclude or [],
    )
    asyncio.run(frontend.serve_forever())


if __name__ == "__main__":
    main()
