# -*- coding: utf-8 -*-
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional


DB_PATH = os.getenv("DB_PATH", "chat_history.db")
SECRET_KEY = os.getenv("JWT_SECRET", secrets.token_hex(32))
TOKEN_EXPIRE_DAYS = int(os.getenv("TOKEN_EXPIRE_DAYS", "30"))
PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "10"))
PASSWORD_HASH_ITERATIONS = int(os.getenv("PASSWORD_HASH_ITERATIONS", "310000"))
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))


class UserManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()
        self._bootstrap_admin()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False); self._local.conn.execute('PRAGMA journal_mode=WAL;')
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE,
                    password TEXT NOT NULL,
                    display_name TEXT,
                    role TEXT DEFAULT 'user',
                    created_at TEXT NOT NULL,
                    last_login TEXT,
                    is_active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS user_tokens (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS login_attempts (
                    username TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tokens_user ON user_tokens(user_id);
                CREATE INDEX IF NOT EXISTS idx_tokens_hash ON user_tokens(token_hash);
                CREATE INDEX IF NOT EXISTS idx_memories_user ON user_memories(user_id, type);
            """)
            try:
                conn.execute("ALTER TABLE user_tokens ADD COLUMN token_hash TEXT")
            except Exception:
                pass
            conn.commit()

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            PASSWORD_HASH_ITERATIONS,
        )
        return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${digest.hex()}"

    def _verify_password(self, password: str, hashed: str) -> bool:
        try:
            if hashed.startswith("pbkdf2_sha256$"):
                _, rounds, salt, digest = hashed.split("$", 3)
                iterations = int(rounds)
            else:
                salt, digest = hashed.split(":", 1)
                iterations = 100000
            check = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                iterations,
            )
            return hmac.compare_digest(digest, check.hex())
        except Exception:
            return False

    def _validate_password(self, password: str) -> Optional[str]:
        if len(password) < PASSWORD_MIN_LENGTH:
            return f"รหัสผ่านต้องมีอย่างน้อย {PASSWORD_MIN_LENGTH} ตัวอักษร"
        if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            return "รหัสผ่านต้องมีทั้งตัวอักษรและตัวเลข"
        return None

    def _token_hash(self, token: str) -> str:
        return hmac.new(
            SECRET_KEY.encode("utf-8"),
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _login_state(self, username: str) -> Dict:
        row = self._conn().execute(
            "SELECT attempts, locked_until FROM login_attempts WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            return {"locked": False, "attempts": 0}
        locked_until = row["locked_until"]
        if locked_until and datetime.fromisoformat(locked_until) > datetime.now():
            return {"locked": True, "attempts": row["attempts"], "locked_until": locked_until}
        return {"locked": False, "attempts": row["attempts"]}

    def _record_login_failure(self, username: str):
        now = datetime.now()
        attempts = int(self._login_state(username).get("attempts", 0)) + 1
        locked_until = None
        if attempts >= LOGIN_MAX_ATTEMPTS:
            locked_until = (now + timedelta(minutes=LOGIN_LOCK_MINUTES)).isoformat()
        conn = self._conn()
        conn.execute("""
            INSERT INTO login_attempts (username, attempts, locked_until, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                attempts = excluded.attempts,
                locked_until = excluded.locked_until,
                updated_at = excluded.updated_at
        """, (username, attempts, locked_until, now.isoformat()))
        conn.commit()

    def _clear_login_failures(self, username: str):
        conn = self._conn()
        conn.execute("DELETE FROM login_attempts WHERE username = ?", (username,))
        conn.commit()

    def _create_token(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now()
        expires_at = now + timedelta(days=TOKEN_EXPIRE_DAYS)
        token_hash = self._token_hash(token)
        conn = self._conn()
        
        # [แก้ไขแล้ว] บันทึก token_hash ลงใน primary key ป้องกัน UNIQUE constraint failed
        conn.execute(
            """
            INSERT INTO user_tokens (token, user_id, token_hash, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token_hash, user_id, token_hash, now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()
        return token

    def verify_token(self, token: str) -> Optional[Dict]:
        if not token:
            return None
        conn = self._conn()
        token_hash = self._token_hash(token)
        row = conn.execute("""
            SELECT u.id, u.username, u.email, u.display_name, u.role, t.expires_at
            FROM user_tokens t
            JOIN users u ON t.user_id = u.id
            WHERE (t.token_hash = ? OR t.token = ?) AND u.is_active = 1
        """, (token_hash, token)).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row["expires_at"]) < datetime.now():
            conn.execute("DELETE FROM user_tokens WHERE token_hash = ? OR token = ?", (token_hash, token))
            conn.commit()
            return None
        return dict(row)

    def register(self, username: str, password: str, email: str = None, display_name: str = None) -> Dict:
        username = username.lower().strip()
        if len(username) < 3:
            return {"ok": False, "error": "ชื่อผู้ใช้ต้องมีอย่างน้อย 3 ตัวอักษร"}
        password_error = self._validate_password(password)
        if password_error:
            return {"ok": False, "error": password_error}

        user_id = secrets.token_hex(8)
        now = datetime.now().isoformat()
        try:
            conn = self._conn()
            conn.execute("""
                INSERT INTO users (id, username, email, password, display_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, username, email, self._hash_password(password), display_name or username, now))
            conn.commit()
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "ชื่อผู้ใช้หรืออีเมลนี้มีอยู่แล้ว"}

        token = self._create_token(user_id)
        return {
            "ok": True,
            "token": token,
            "user": {"id": user_id, "username": username, "display_name": display_name or username, "role": "user"},
        }

    def login(self, username: str, password: str) -> Dict:
        username = username.lower().strip()
        if self._login_state(username).get("locked"):
            return {"ok": False, "error": "บัญชีถูกล็อกชั่วคราวจากการใส่รหัสผิดหลายครั้ง"}

        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1",
            (username,),
        ).fetchone()
        if not row or not self._verify_password(password, row["password"]):
            self._record_login_failure(username)
            return {"ok": False, "error": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"}

        self._clear_login_failures(username)
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (datetime.now().isoformat(), row["id"]))
        conn.commit()
        token = self._create_token(row["id"])
        return {
            "ok": True,
            "token": token,
            "user": {"id": row["id"], "username": row["username"], "display_name": row["display_name"], "role": row["role"]},
        }

    def logout(self, token: str):
        conn = self._conn()
        conn.execute("DELETE FROM user_tokens WHERE token_hash = ? OR token = ?", (self._token_hash(token), token))
        conn.commit()

    def get_user(self, user_id: str) -> Optional[Dict]:
        row = self._conn().execute(
            "SELECT id, username, email, display_name, role, created_at, last_login, is_active FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_display_name(self, user_id: str, name: str):
        conn = self._conn()
        conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (name, user_id))
        conn.commit()

    def change_password(self, user_id: str, old_pw: str, new_pw: str) -> Dict:
        row = self._conn().execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not self._verify_password(old_pw, row["password"]):
            return {"ok": False, "error": "รหัสผ่านเดิมไม่ถูกต้อง"}
        password_error = self._validate_password(new_pw)
        if password_error:
            return {"ok": False, "error": password_error}
        conn = self._conn()
        conn.execute("UPDATE users SET password = ? WHERE id = ?", (self._hash_password(new_pw), user_id))
        conn.execute("DELETE FROM user_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return {"ok": True}

    def list_users(self) -> list:
        rows = self._conn().execute("""
            SELECT id, username, email, display_name, role, created_at, last_login, is_active
            FROM users ORDER BY created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def deactivate_user(self, user_id: str):
        conn = self._conn()
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM user_tokens WHERE user_id = ?", (user_id,))
        conn.commit()

    def activate_user(self, user_id: str):
        conn = self._conn()
        conn.execute("UPDATE users SET is_active = 1 WHERE id = ?", (user_id,))
        conn.commit()

    def set_role(self, user_id: str, role: str):
        if role not in ("user", "admin"):
            raise ValueError("invalid role")
        conn = self._conn()
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()

    def _bootstrap_admin(self):
        username = os.getenv("ADMIN_USERNAME", "").strip().lower()
        password = os.getenv("ADMIN_PASSWORD", "")
        if not username or not password:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if row:
                conn.execute("UPDATE users SET role = 'admin', is_active = 1 WHERE username = ?", (username,))
            else:
                conn.execute("""
                    INSERT INTO users (id, username, password, display_name, role, created_at, is_active)
                    VALUES (?, ?, ?, ?, 'admin', ?, 1)
                """, (secrets.token_hex(8), username, self._hash_password(password), username, datetime.now().isoformat()))
            conn.commit()


user_manager = UserManager()