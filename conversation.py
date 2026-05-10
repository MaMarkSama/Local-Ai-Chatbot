"""
conversation.py — In-memory + SQLite hybrid
ใช้ RAM สำหรับ context window, SQLite สำหรับ persistent history
"""
from typing import List, Dict
import threading
from collections import defaultdict
from database import db


class ConversationManager:
    def __init__(self, max_turns: int = 10):
        self.max_turns   = max_turns
        self._cache: Dict[str, List[Dict]] = defaultdict(list)
        self._lock       = threading.Lock()

    def _load_from_db(self, session_id: str):
        """โหลดประวัติจาก DB เข้า cache ถ้ายังไม่มี"""
        with self._lock:
            if session_id not in self._cache:
                msgs = db.get_messages(session_id, limit=self.max_turns)
                self._cache[session_id] = msgs

    def add_message(self, session_id: str, role: str, content: str, source: str = "line"):
        self._load_from_db(session_id)
        with self._lock:
            self._cache[session_id].append({"role": role, "content": content})
            max_msg = self.max_turns * 2
            if len(self._cache[session_id]) > max_msg:
                self._cache[session_id] = self._cache[session_id][-max_msg:]
        db.add_message(session_id, role, content, source)

    def get_messages(self, session_id: str) -> List[Dict]:
        self._load_from_db(session_id)
        with self._lock:
            return list(self._cache[session_id])

    def clear(self, session_id: str):
        with self._lock:
            self._cache[session_id] = []
        db.clear_session(session_id)

    def get_turn_count(self, session_id: str) -> int:
        with self._lock:
            return len(self._cache.get(session_id, [])) // 2