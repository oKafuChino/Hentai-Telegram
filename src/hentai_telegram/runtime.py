from contextlib import closing, contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3


@contextmanager
def instance_lock(path: Path):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise PermissionError(f"无法创建运行目录 {path.parent}，请检查目录权限：{exc}") from exc

    try:
        stream = path.open("a+b")
    except PermissionError as exc:
        raise PermissionError(
            f"无法访问锁文件 {path}（权限不足）。"
            f"请检查目录/文件所有者与权限，例如在宿主机执行: sudo chown -R 1000:1000 data/"
        ) from exc

    with stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another Hentai-Telegram instance is running") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def database_path(url):
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        raise ValueError("Only SQLite file databases are supported")
    return Path(url[len(prefix):]).resolve()


def backup_database(source, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Backup destination must not exist")
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"level": record.levelname, "event": record.getMessage()}, ensure_ascii=False)
