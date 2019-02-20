#!/usr/bin/env python3

from pathlib import Path

from setuptools import setup

from httpprocessproxy import __version__

long_description = (Path(__file__).parent / "README.rst").read_text("utf-8")

setup(
    name="http-process-proxy",
    version=__version__,
    description="HTTP reverse proxy to live-reload a web server",
    long_description=long_description,
    long_description_content_type="text/x-rst",
    packages=["httpprocessproxy"],
    package_data={"httpprocessproxy": ["livereload.js"]},
    author="Adam Hooper",
    author_email="adam@adamhooper.com",
    url="https://github.com/CJWorkbench/http-process-proxy",
    entry_points={
        "console_scripts": ["http-process-proxy = httpprocessproxy.__main__:main"]
    },
    license="MIT",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "License :: OSI Approved :: MIT License",
        "Topic :: Internet :: WWW/HTTP :: HTTP Servers",
        "Topic :: Software Development :: Build Tools",
    ],
    install_requires=["pywatchman>=1.4.1", "websockets>=7.0"],
    extras_require={"dev": ["black", "isort", "pyflakes"]},
)
