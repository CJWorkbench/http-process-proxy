#!/usr/bin/env python3

from setuptools import setup

setup(
    name='http-process-proxy',
    description='HTTP reverse proxy to live-reload a web server',
    version='0.0.1',
    packages=['httpprocessproxy'],
    author='Adam Hooper',
    author_email='adam@adamhooper.com',
    url='https://github.com/CJWorkbench/http-process-proxy',
    entry_points={
        'console_scripts': [
            'http-process-proxy = httpprocessproxy.__main__:main'
        ]
    },
    license='AGPL-3.0',
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "License :: OSI Approved :: GNU Affero General Public License v3",
        "Topic :: Internet :: WWW/HTTP :: HTTP Servers",
        "Topic :: Software Development :: Build Tools",
    ],
    install_requires=['pywatchman>=1.4.1']
)
