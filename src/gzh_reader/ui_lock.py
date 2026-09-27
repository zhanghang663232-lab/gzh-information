"""A machine-wide lock for the single physical Mac WeChat interface."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


DEFAULT_LOCK = Path.home() / "Library" / "Application Support" / "gzh-information" / "ui-control.lock"


@contextmanager
def ui_control_lock(path: Path | None = None):
    target = path or DEFAULT_LOCK
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("已有另一个任务正在操作 Mac 微信；请等它结束后再试") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
