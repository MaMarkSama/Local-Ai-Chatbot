from collections import defaultdict
from typing import List, Dict
import threading


class ConversationManager:
    """
    จัดการประวัติการสนทนาแบบ in-memory ต่อ user
    รองรับ thread-safe สำหรับ concurrent requests
    """

    def __init__(self, max_turns: int = 10):
        """
        max_turns: จำนวนคู่สนทนา (user+assistant) สูงสุดที่เก็บ
        """
        self.max_turns   = max_turns
        self._histories: Dict[str, List[Dict]] = defaultdict(list)
        self._lock       = threading.Lock()

    def add_message(self, user_id: str, role: str, content: str):
        with self._lock:
            self._histories[user_id].append({"role": role, "content": content})
            # ตัดประวัติเก่าออก (เก็บแค่ max_turns คู่ล่าสุด)
            max_messages = self.max_turns * 2
            if len(self._histories[user_id]) > max_messages:
                self._histories[user_id] = self._histories[user_id][-max_messages:]

    def get_messages(self, user_id: str) -> List[Dict]:
        with self._lock:
            return list(self._histories[user_id])

    def clear(self, user_id: str):
        with self._lock:
            self._histories[user_id] = []

    def get_turn_count(self, user_id: str) -> int:
        with self._lock:
            return len(self._histories[user_id]) // 2
