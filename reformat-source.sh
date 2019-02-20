#!/bin/sh

# Reformats source code to adhere to standards.

set -e
set -x

pyflakes .
isort --recursive setup.py httpprocessproxy
black .
