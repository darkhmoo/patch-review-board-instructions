from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(lock_path: Path, timeout: float = 10.0, poll: float = 0.1):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode())
            break
        except FileExistsError:
            if time.time() - start > timeout:
                raise TimeoutError(f"lock timeout: {lock_path}")
            time.sleep(poll)

    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def atomic_write_text(path: Path, content: str, mode: int = 0o600, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding=encoding) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.chmod(tmp_path, mode)
    os.replace(tmp_path, path)


def atomic_write_json(path: Path, obj, mode: int = 0o600, ensure_ascii: bool = False, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent), mode=mode)
