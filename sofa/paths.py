# SPDX-License-Identifier: GPL-3.0-only
"""Where Sofa keeps its files (XDG base directories)."""

import os

APP = "sofa-tv-browser"
HERE = os.path.dirname(os.path.abspath(__file__))


def _xdg(var: str, fallback: str) -> str:
    base = os.environ.get(var) or os.path.expanduser(fallback)
    d = os.path.join(base, APP)
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def config_dir() -> str:
    return _xdg("XDG_CONFIG_HOME", "~/.config")


def data_dir() -> str:
    return _xdg("XDG_DATA_HOME", "~/.local/share")


def cache_dir() -> str:
    return _xdg("XDG_CACHE_HOME", "~/.cache")


def sub(base: str, *names: str) -> str:
    d = os.path.join(base, *names)
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def resource(*names: str) -> str:
    return os.path.join(HERE, *names)
