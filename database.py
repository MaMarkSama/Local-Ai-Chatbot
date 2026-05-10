"""
database.py — SQLite history manager
เก็บประวัติการสนทนาทั้ง Line Bot และ Web Chat
"""
import sqlite3
import threading
from datetime import datetime
from typing import List, Dict, Optional

DB_PATH = "chat_history.db"


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._local  = threading.local()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    source      TEXT NOT NULL DEFAULT 'webchat',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    title       TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT    NOT NULL,
                    role        TEXT    NOT NULL,
                    content     TEXT    NOT NULL,
                    created_at  TEXT    NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, id);
            """)
            # migrate: add title column if missing
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
                conn.commit()
            except Exception:
                pass

    def _has_col(self, col: str) -> bool:
        conn = self._conn()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        return col in cols

    def ensure_session(self, session_id: str, source: str = "webchat"):
        now  = datetime.now().isoformat()
        conn = self._conn()
        conn.execute("""
            INSERT INTO sessions (id, source, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
        """, (session_id, source, now, now))
        conn.commit()

    def rename_session(self, session_id: str, title: str):
        conn = self._conn()
        conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
        conn.commit()

    def add_message(self, session_id: str, role: str, content: str, source: str = "webchat"):
        self.ensure_session(session_id, source)
        now  = datetime.now().isoformat()
        conn = self._conn()
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now)
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        conn.commit()

    def get_messages(self, session_id: str, limit: int = 20) -> List[Dict]:
        conn = self._conn()
        rows = conn.execute("""
            SELECT role, content FROM (
                SELECT role, content, id FROM messages
                WHERE session_id = ?
                ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
        """, (session_id, limit * 2)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def clear_session(self, session_id: str):
        conn = self._conn()
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.commit()

    def delete_session(self, session_id: str):
        conn = self._conn()
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()

    def get_sessions(self, source: Optional[str] = None, limit: int = 100) -> List[Dict]:
        conn  = self._conn()
        query = """
            SELECT s.id, s.source, s.created_at, s.updated_at, s.title,
                   COUNT(m.id) as msg_count,
                   MAX(CASE WHEN m.role='user' THEN m.content END) as last_user_msg
            FROM sessions s
            LEFT JOIN messages m ON s.id = m.session_id
        """
        args = []
        if source:
            query += " WHERE s.source = ?"
            args.append(source)
        query += " GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(query, args).fetchall()
        return [dict(r) for r in rows]

    def get_session_messages(self, session_id: str) -> List[Dict]:
        conn = self._conn()
        rows = conn.execute("""
            SELECT role, content, created_at FROM messages
            WHERE session_id = ? ORDER BY id ASC
        """, (session_id,)).fetchall()
        return [dict(r) for r in rows]

    def search_messages(self, keyword: str, limit: int = 30) -> List[Dict]:
        conn = self._conn()
        rows = conn.execute("""
            SELECT m.session_id, m.role, m.content, m.created_at, s.source
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.content LIKE ?
            ORDER BY m.id DESC LIMIT ?
        """, (f"%{keyword}%", limit)).fetchall()
        return [dict(r) for r in rows]


# Singleton
db = Database()