# -*- coding: utf-8 -*-
"""
conversation.py — In-memory + SQLite + Memory Manager hybrid
"""
from typing import List, Dict
import threading
from collections import defaultdict
from database import db


class ConversationManager:
    def __init__(self, max_turns: int = 20):
        self.max_turns = max_turns
        self._cache: Dict[str, List[Dict]] = defaultdict(list)
        self._lock  = threading.Lock()

    def _load_from_db(self, session_id: str):
        with self._lock:
            if session_id not in self._cache:
                msgs = db.get_messages(session_id, limit=self.max_turns)
                self._cache[session_id] = msgs

    def add_message(self, session_id: str, role: str, content: str,
                    source: str = "webchat", user_id: str = None):
        self._load_from_db(session_id)
        with self._lock:
            self._cache[session_id].append({"role": role, "content": content})
            max_msg = self.max_turns * 2
            if len(self._cache[session_id]) > max_msg:
                self._cache[session_id] = self._cache[session_id][-max_msg:]
        db.add_message(session_id, role, content, source, user_id=user_id)

    def get_messages(self, session_id: str) -> List[Dict]:
        self._load_from_db(session_id)
        with self._lock:
            return list(self._cache[session_id])

    def get_message_count(self, session_id: str) -> int:
        self._load_from_db(session_id)
        with self._lock:
            return len(self._cache[session_id])

    def clear(self, session_id: str):
        with self._lock:
            self._cache[session_id] = []
        db.clear_session(session_id)

    def compress(self, session_id: str, summary: str, keep_last: int = 6):
        """แทนที่ messages เก่าด้วย summary"""
        with self._lock:
            recent = self._cache[session_id][-keep_last:]
            self._cache[session_id] = recent
        # บันทึก summary ลง DB ด้วย
        db.clear_session(session_id)
        for m in self._cache[session_id]:
            db.add_message(session_id, m["role"], m["content"])
