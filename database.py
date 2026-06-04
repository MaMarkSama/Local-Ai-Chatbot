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
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False); self._local.conn.execute("PRAGMA journal_mode=WAL;")
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT,
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

                CREATE TABLE IF NOT EXISTS user_memories (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    TEXT NOT NULL,
                    session_id TEXT,
                    type       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_user
                    ON user_memories(user_id, type);
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

    def ensure_session(self, session_id: str, source: str = "webchat",
                       user_id: str = None):
        now  = datetime.now().isoformat()
        conn = self._conn()
        # Migration: หากเจ้าของเดิมเป็น anon และคนใหม่เป็น user จริง ให้ย้ายความจำตามมาด้วย
        old_owner = self.get_session_owner(session_id)
        if user_id and old_owner and old_owner.startswith("anon_") and old_owner != user_id:
            conn.execute("UPDATE user_memories SET user_id = ? WHERE user_id = ?", (user_id, old_owner))
            conn.execute("UPDATE sessions SET user_id = ? WHERE id = ?", (user_id, session_id))

        conn.execute("""
            INSERT INTO sessions (id, user_id, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at = excluded.updated_at,
                user_id = CASE 
                    WHEN excluded.user_id IS NOT NULL AND (sessions.user_id IS NULL OR sessions.user_id LIKE 'anon_%') 
                    THEN excluded.user_id 
                    ELSE sessions.user_id 
                 END
        """, (session_id, user_id, source, now, now))
        conn.commit()

    def rename_session(self, session_id: str, title: str):
        conn = self._conn()
        conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
        conn.commit()

    def add_message(self, session_id: str, role: str, content: str, source: str = "webchat",
                    user_id: str = None):
        self.ensure_session(session_id, source, user_id=user_id)
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

    def get_sessions(self, source: Optional[str] = None, limit: int = 100, user_id: str = None) -> List[Dict]:
        conn  = self._conn()
        query = """
            SELECT s.id, s.source, s.created_at, s.updated_at, s.title,
                   COUNT(m.id) as msg_count,
                   MAX(CASE WHEN m.role='user' THEN m.content END) as last_user_msg
            FROM sessions s
            LEFT JOIN messages m ON s.id = m.session_id
            WHERE 1=1
        """
        args = []
        if source:
            query += " AND s.source = ?"
            args.append(source)
        if user_id:
            query += " AND s.user_id = ?"
            args.append(user_id)
        else:
            # ถ้าไม่ระบุ user (Guest) ให้เห็นเฉพาะ session ที่ไม่มีเจ้าของ (ซึ่งจะถูกกรองต่อในหน้าเว็บ)
            query += " AND (s.user_id IS NULL OR s.user_id LIKE 'anon_%')"
            
        query += " GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(query, args).fetchall()
        return [dict(r) for r in rows]

    def get_session_owner(self, session_id: str) -> Optional[str]:
        row = self._conn().execute(
            "SELECT user_id FROM sessions WHERE id = ?",
            (session_id,)
        ).fetchone()
        return row["user_id"] if row else None

    def get_session_messages(self, session_id: str) -> List[Dict]:
        conn = self._conn()
        rows = conn.execute("""
            SELECT role, content, created_at FROM messages
            WHERE session_id = ? ORDER BY id ASC
        """, (session_id,)).fetchall()
        return [dict(r) for r in rows]

    def search_messages(self, keyword: str, limit: int = 30, user_id: str = None) -> List[Dict]:
        conn = self._conn()
        query = """
            SELECT m.session_id, m.role, m.content, m.created_at, s.source
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.content LIKE ?
        """
        args = [f"%{keyword}%"]
        if user_id:
            query += " AND s.user_id = ?"
            args.append(user_id)
        query += " ORDER BY m.id DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(query, args).fetchall()
        return [dict(r) for r in rows]


    # ── Memory ────────────────────────────────────────────────────────────
    def save_memory(self, user_id: str, session_id: Optional[str],
                    mem_type: str, content: str):
        """บันทึก memory (summary หรือ fact)"""
        now  = datetime.now().isoformat()
        conn = self._conn()
        conn.execute("""
            INSERT INTO user_memories (user_id, session_id, type, content, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, session_id, mem_type, content, now))
        conn.commit()

    def get_memories(self, user_id: str, session_id: Optional[str] = None,
                     limit: int = 10) -> List[Dict]:
        """ดึง memory ของ user"""
        conn  = self._conn()
        query = """
            SELECT type, content, created_at FROM user_memories
            WHERE user_id = ?
        """
        args = [user_id]
        if session_id:
            query += " AND (session_id = ? OR session_id IS NULL)"
            args.append(session_id)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(query, args).fetchall()
        return [dict(r) for r in reversed(rows)]

    def clear_memories(self, user_id: str, session_id: Optional[str] = None):
        conn = self._conn()
        if session_id:
            conn.execute(
                "DELETE FROM user_memories WHERE user_id = ? AND session_id = ?",
                (user_id, session_id))
        else:
            conn.execute("DELETE FROM user_memories WHERE user_id = ?", (user_id,))
        conn.commit()

    def get_user_sessions(self, user_id: str, source: str = "webchat",
                          limit: int = 100) -> list:
        """ดึง sessions ของ user คนนี้เท่านั้น"""
        conn  = self._conn()
        rows  = conn.execute("""
            SELECT s.id, s.source, s.created_at, s.updated_at, s.title,
                   COUNT(m.id) as msg_count,
                   MAX(CASE WHEN m.role='user' THEN m.content END) as last_user_msg
            FROM sessions s
            LEFT JOIN messages m ON s.id = m.session_id
            WHERE s.user_id = ? AND s.source = ?
            GROUP BY s.id
            ORDER BY s.updated_at DESC LIMIT ?
        """, (user_id, source, limit)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> Dict:
        conn = self._conn()
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] if self._table_exists("users") else 0
        active_users = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0] if self._table_exists("users") else 0
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        memories = conn.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0]
        vectors = conn.execute("SELECT COUNT(*) FROM document_vectors").fetchone()[0] if self._table_exists("document_vectors") else 0
        return {
            "users": users,
            "active_users": active_users,
            "sessions": sessions,
            "messages": messages,
            "memories": memories,
            "document_vectors": vectors,
        }

    def _table_exists(self, table: str) -> bool:
        row = self._conn().execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table,)
        ).fetchone()
        return row is not None

    def export_snapshot(self, limit: int = 5000) -> Dict:
        conn = self._conn()
        payload = {"exported_at": datetime.now().isoformat(), "tables": {}}
        for table in ("users", "sessions", "messages", "user_memories", "document_vectors"):
            if not self._table_exists(table):
                payload["tables"][table] = []
                continue
            rows = conn.execute(f"SELECT * FROM {table} LIMIT ?", (limit,)).fetchall()
            payload["tables"][table] = [dict(r) for r in rows]
        return payload

# Singleton
db = Database()
