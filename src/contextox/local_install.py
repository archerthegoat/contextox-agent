"""Local paths and a single owner for one installed data directory."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path


def static_directory() -> Path:
    bundled = Path(__file__).resolve().parent / "static"
    return bundled if bundled.is_dir() else Path(__file__).resolve().parents[2] / "web" / "dist"


def data_directory() -> Path:
    return Path.home() / "Library" / "Application Support" / "ContextOx"


class InstanceAlreadyRunning(Exception):
    def __init__(self, port: int | None):
        self.port = port


@contextmanager
def own_instance(directory: Path, port: int):
    """Lock before opening SQLite: a second start must never recover live Runs."""
    descriptor = os.open(directory / "instance.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                metadata = json.load(handle)
                actual = metadata.get("port")
                actual = actual if type(actual) is int and 1 <= actual <= 65535 else None
            except (ValueError, AttributeError):
                actual = None
            raise InstanceAlreadyRunning(actual) from None
        try:
            handle.seek(0)
            handle.truncate()
            json.dump({"pid": os.getpid(), "port": port}, handle)
            handle.flush()
            os.fsync(handle.fileno())
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            handle.flush()
            fcntl.flock(handle, fcntl.LOCK_UN)
