import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from hentai_telegram.domain.models import Chapter, Manga, Page, normalize_tags

TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


def decode_manga(raw) -> Manga:
    data = json.loads(raw) if isinstance(raw, str) else raw
    chapters = tuple(
        Chapter(
            source_id=c["source_id"],
            ordinal=c["ordinal"],
            title=c["title"],
            pages=tuple(Page(**p) for p in c.get("pages", ())),
            telegraph_url=c.get("telegraph_url"),
        )
        for c in data.get("chapters", ())
    )
    return Manga(
        source=data["source"],
        source_id=data["source_id"],
        title=data["title"],
        authors=tuple(data.get("authors", ())),
        chapters=chapters,
        cover_url=data.get("cover_url"),
        tags=normalize_tags(data.get("tags")),
        index_url=data.get("index_url"),
    )


def encode_manga(manga: Manga) -> str:
    return json.dumps(
        {
            "source": manga.source,
            "source_id": manga.source_id,
            "title": manga.title,
            "authors": list(manga.authors),
            "cover_url": manga.cover_url,
            "tags": list(manga.tags),
            "index_url": manga.index_url,
            "chapters": [
                {
                    "source_id": c.source_id,
                    "ordinal": c.ordinal,
                    "title": c.title,
                    "telegraph_url": c.telegraph_url,
                    "pages": [asdict(p) for p in c.pages],
                }
                for c in manga.chapters
            ],
        },
        ensure_ascii=False,
    )


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version < 20:
            with self.transaction() as db:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS manga (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        authors TEXT NOT NULL DEFAULT '[]',
                        tags TEXT NOT NULL DEFAULT '[]',
                        cover_url TEXT,
                        index_url TEXT,
                        created_at REAL NOT NULL,
                        UNIQUE(source, source_id)
                    )
                """)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS chapters (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        manga_id INTEGER NOT NULL REFERENCES manga(id),
                        source_id TEXT NOT NULL,
                        ordinal INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        pages TEXT,
                        telegraph_url TEXT,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        UNIQUE(manga_id, source_id)
                    )
                """)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        manga_id INTEGER NOT NULL REFERENCES manga(id),
                        snapshot TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        chat_id INTEGER NOT NULL,
                        message_id INTEGER,
                        channel_message_id INTEGER,
                        status TEXT NOT NULL DEFAULT 'QUEUED',
                        phase TEXT NOT NULL DEFAULT 'queued',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        next_at REAL NOT NULL DEFAULT 0,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        error_code TEXT,
                        error TEXT,
                        progress TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)
                # Migrations if existing database from older schemas
                cols_chapters = {r[1] for r in db.execute("PRAGMA table_info(chapters)").fetchall()}
                if "telegraph_url" not in cols_chapters:
                    db.execute("ALTER TABLE chapters ADD COLUMN telegraph_url TEXT")
                cols_tasks = {r[1] for r in db.execute("PRAGMA table_info(tasks)").fetchall()}
                if "channel_message_id" not in cols_tasks:
                    db.execute("ALTER TABLE tasks ADD COLUMN channel_message_id INTEGER")
                cols_manga = {r[1] for r in db.execute("PRAGMA table_info(manga)").fetchall()}
                if "index_url" not in cols_manga:
                    db.execute("ALTER TABLE manga ADD COLUMN index_url TEXT")

                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_manga ON tasks(manga_id) WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED')")
                db.execute("CREATE INDEX IF NOT EXISTS ready_tasks ON tasks(status, next_at)")
                db.execute("PRAGMA user_version=20")

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def get(self, task_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def check_submission(self, source: str, source_id: str, user_id: int, force: bool = False):
        row = self.connection.execute(
            """SELECT t.id, t.status, t.user_id FROM tasks t
               JOIN manga m ON m.id = t.manga_id
               WHERE m.source = ? AND m.source_id = ?
               AND t.status NOT IN ('FAILED', 'CANCELLED')
               ORDER BY t.created_at DESC LIMIT 1""",
            (source, source_id),
        ).fetchone()
        if row:
            if row["status"] == "COMPLETED":
                if not force:
                    raise ValueError("该漫画已完成并发布到频道。如需强制重新抓取发布，请使用：/jm <ID> force")
                return
            raise ValueError(f"该漫画已有正在处理的任务 (状态: {row['status']})")

    def submit(
        self,
        manga: Manga,
        user_id: int,
        chat_id: int,
        message_id: int | None = None,
        retry_of: str | None = None,
        force: bool = False,
    ) -> dict:
        now = time.time()
        with self.transaction() as db:
            manga_row = db.execute(
                "SELECT id FROM manga WHERE source=? AND source_id=?",
                (manga.source, manga.source_id),
            ).fetchone()
            if manga_row:
                manga_id = manga_row["id"]
                db.execute(
                    "UPDATE manga SET title=?, authors=?, tags=?, cover_url=? WHERE id=?",
                    (
                        manga.title,
                        json.dumps(list(manga.authors), ensure_ascii=False),
                        json.dumps(list(manga.tags), ensure_ascii=False),
                        manga.cover_url,
                        manga_id,
                    ),
                )
                if force:
                    db.execute("UPDATE chapters SET telegraph_url=NULL, status='PENDING' WHERE manga_id=?", (manga_id,))
            else:
                cursor = db.execute(
                    "INSERT INTO manga(source, source_id, title, authors, tags, cover_url, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        manga.source,
                        manga.source_id,
                        manga.title,
                        json.dumps(list(manga.authors), ensure_ascii=False),
                        json.dumps(list(manga.tags), ensure_ascii=False),
                        manga.cover_url,
                        now,
                    ),
                )
                manga_id = cursor.lastrowid

            for ch in manga.chapters:
                db.execute(
                    "INSERT INTO chapters(manga_id, source_id, ordinal, title) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(manga_id, source_id) DO UPDATE SET ordinal=excluded.ordinal, title=excluded.title",
                    (manga_id, ch.source_id, ch.ordinal, ch.title),
                )

            task_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO tasks(id, manga_id, snapshot, user_id, chat_id, message_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?)",
                (task_id, manga_id, encode_manga(manga), user_id, chat_id, message_id, now, now),
            )
            return dict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def update(self, task_id: str, **kwargs):
        if not kwargs:
            return
        kwargs["updated_at"] = time.time()
        clause = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [task_id]
        with self.transaction() as db:
            db.execute(f"UPDATE tasks SET {clause} WHERE id=?", values)

    def finish(self, task_id: str, status: str, error_code: str | None = None, error: str | None = None):
        self.update(task_id, status=status, error_code=error_code, error=error)

    def request_cancel(self, task_id: str, user_id: int):
        task = self.get(task_id)
        if not task:
            raise ValueError("任务不存在")
        if task["status"] in TERMINAL:
            raise ValueError("任务已经结束，无法取消")
        if task["status"] in ("QUEUED", "RETRY_WAIT"):
            self.update(task_id, cancel_requested=1, status="CANCELLED", error_code="CANCELLED", error="任务已取消")
        else:
            self.update(task_id, cancel_requested=1, status="CANCEL_REQUESTED")

    def next_task(self) -> dict | None:
        now = time.time()
        row = self.connection.execute(
            """SELECT * FROM tasks
               WHERE status='QUEUED' OR (status='RETRY_WAIT' AND next_at <= ?)
               ORDER BY created_at ASC LIMIT 1""",
            (now,),
        ).fetchone()
        return dict(row) if row else None

    def chapters(self, manga_id: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM chapters WHERE manga_id=? ORDER BY ordinal ASC, id ASC",
            (manga_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_chapter(self, chapter_id: int, **kwargs):
        if not kwargs:
            return
        clause = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [chapter_id]
        with self.transaction() as db:
            db.execute(f"UPDATE chapters SET {clause} WHERE id=?", values)

    def update_manga(self, manga_id: int, **kwargs):
        if not kwargs:
            return
        clause = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [manga_id]
        with self.transaction() as db:
            db.execute(f"UPDATE manga SET {clause} WHERE id=?", values)

    def recover(self):
        """Reset in-flight tasks back to QUEUED on worker restart."""
        with self.transaction() as db:
            db.execute(
                "UPDATE tasks SET status='QUEUED', progress='正在准备重新处理' "
                "WHERE status IN ('DOWNLOADING', 'UPLOADING', 'PUBLISHING', 'CANCEL_REQUESTED')"
            )

    def tasks(self, limit: int = 20, active_only: bool = False) -> list[dict]:
        where = "WHERE status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')" if active_only else ""
        rows = self.connection.execute(
            f"""SELECT t.*, m.title, m.source_id as jm_id FROM tasks t
                JOIN manga m ON m.id = t.manga_id
                {where}
                ORDER BY t.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self.transaction() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_manga(self, source: str, source_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM manga WHERE source=? AND source_id=?",
            (source, source_id),
        ).fetchone()
        return dict(row) if row else None

    def get_manga_with_details(self, source: str, source_id: str) -> dict | None:
        """Fetch a manga with its chapters and tasks from the database without deleting."""
        row = self.connection.execute(
            "SELECT * FROM manga WHERE source=? AND source_id=?",
            (source, source_id),
        ).fetchone()
        if not row:
            return None
        manga_data = dict(row)
        manga_id = manga_data["id"]

        tasks_rows = self.connection.execute("SELECT * FROM tasks WHERE manga_id=?", (manga_id,)).fetchall()
        manga_data["tasks"] = [dict(r) for r in tasks_rows]

        chapters_rows = self.connection.execute("SELECT * FROM chapters WHERE manga_id=?", (manga_id,)).fetchall()
        manga_data["chapters"] = [dict(r) for r in chapters_rows]

        return manga_data

    def delete_manga(self, source: str, source_id: str) -> dict | None:
        """Delete a manga and its chapters and tasks from the database."""
        with self.transaction() as db:
            manga_row = db.execute(
                "SELECT * FROM manga WHERE source=? AND source_id=?",
                (source, source_id),
            ).fetchone()
            if not manga_row:
                return None
            manga_data = dict(manga_row)
            manga_id = manga_data["id"]

            tasks_rows = db.execute("SELECT * FROM tasks WHERE manga_id=?", (manga_id,)).fetchall()
            manga_data["tasks"] = [dict(r) for r in tasks_rows]

            chapters_rows = db.execute("SELECT * FROM chapters WHERE manga_id=?", (manga_id,)).fetchall()
            manga_data["chapters"] = [dict(r) for r in chapters_rows]

            db.execute("DELETE FROM tasks WHERE manga_id=?", (manga_id,))
            db.execute("DELETE FROM chapters WHERE manga_id=?", (manga_id,))
            db.execute("DELETE FROM manga WHERE id=?", (manga_id,))

            return manga_data
