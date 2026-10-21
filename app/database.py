import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(path: str):
    db = sqlite3.connect(path, timeout=10, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        # A clear startup error beats a later search failure on a custom SQLite build.
        try:
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_check USING fts5(text)")
        except sqlite3.OperationalError as exc:
            raise RuntimeError("This Python SQLite build must include FTS5. Use standard CPython 3.12+.") from exc
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE IF NOT EXISTS tokens (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS token_user ON tokens(user_id);
        CREATE TABLE IF NOT EXISTS login_attempts (
            username TEXT PRIMARY KEY, failures INTEGER NOT NULL, reset_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            UNIQUE(user_id, name)
        );
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            title TEXT NOT NULL, text TEXT NOT NULL, content_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','indexing','ready','failed')),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            UNIQUE(collection_id, content_hash)
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_token TEXT, lease_until REAL, error TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS job_poll ON jobs(status, lease_until);
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chunk_number INTEGER NOT NULL, text TEXT NOT NULL,
            UNIQUE(document_id, chunk_number)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text, content='chunks', content_rowid='id', tokenize='unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS chunks_insert AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid,text) VALUES(new.id,new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_delete AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts,rowid,text) VALUES('delete',old.id,old.text);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_update AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts,rowid,text) VALUES('delete',old.id,old.text);
            INSERT INTO chunks_fts(rowid,text) VALUES(new.id,new.text);
        END;
        """)
