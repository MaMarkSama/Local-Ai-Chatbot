# -*- coding: utf-8 -*-
import os
import re
import time
import hashlib
import httpx
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

OLLAMA_NUM_CTX  = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "gemma3:9b")

# ── Keyword shortcut tables ────────────────────────────────────────────────
# ถ้า match pattern เหล่านี้ → ตัดสินใจได้ทันทีโดยไม่ต้องเรียก Ollama

_WEB_PATTERNS = re.compile(
    r"(วันนี้|พรุ่งนี้|เมื่อวาน|ตอนนี้|ล่าสุด|ปัจจุบัน|สภาพอากาศ|ราคา|หุ้น|ข่าว"
    r"|weather|today|latest|current|price|news|stock|forex|crypto"
    r"|เรต|ค่าเงิน|ดอลลาร์|บาท|ยูโร|เยน"
    r"|ผล(?:การแข่งขัน|บอล|เลือกตั้ง)|score|result"
    r"|เปิด(?:ทำการ|ปิด)|เวลาทำการ|holiday|วันหยุด)",
    re.IGNORECASE | re.UNICODE,
)

_LIB_PATTERNS = re.compile(
    r"(ไฟล์|เอกสาร|คู่มือ|รายงาน|ที่(?:เคย)?ส่ง|ในคลัง|ที่อัปโหลด|ที่แนบ"
    r"|document|file|manual|report|uploaded|attachment"
    r"|pdf|xlsx|docx|csv"
    r"|ตามเอกสาร|จากไฟล์|ในเอกสาร)",
    re.IGNORECASE | re.UNICODE,
)

_CHAT_PATTERNS = re.compile(
    r"^(สวัสดี|หวัดดี|ดีครับ|ดีค่ะ|hello|hi|hey"
    r"|ขอบคุณ|thank|thanks"
    r"|โอเค|ok|okay"
    r"|ช่วยเขียน|เขียน(?:โค้ด|code)|แปลง|สรุป(?!ไฟล์)"
    r"|(?:คือ|แปลว่า|หมายความ|อธิบาย))",
    re.IGNORECASE | re.UNICODE,
)

# ── LRU cache (simple time-based, thread-safe enough for async) ────────────
_CACHE: Dict[str, Tuple[Tuple[bool, bool], float]] = {}
_CACHE_MAX   = 256   # entries
_CACHE_TTL   = 300   # seconds (5 min)


def _cache_key(text: str) -> str:
    # normalize: lowercase + ย่อ whitespace + hash ถ้ายาวเกิน
    normalized = re.sub(r"\s+", " ", text.strip().lower())[:120]
    return hashlib.md5(normalized.encode("utf-8", errors="ignore")).hexdigest()


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    return None


def _cache_set(key: str, value: Tuple[bool, bool]):
    if len(_CACHE) >= _CACHE_MAX:
        # evict oldest 25%
        oldest = sorted(_CACHE.items(), key=lambda x: x[1][1])[:_CACHE_MAX // 4]
        for k, _ in oldest:
            _CACHE.pop(k, None)
    _CACHE[key] = (value, time.time())


class SmartRouter:
    def __init__(self):
        self.url   = OLLAMA_BASE_URL
        self.model = OLLAMA_MODEL

    async def decide_tools(self, user_message: str) -> Tuple[bool, bool]:
        """
        วิเคราะห์คำถามของผู้ใช้เพื่อตัดสินใจว่าต้องใช้ Web Search หรือ Library Search หรือไม่
        คืนค่า: (need_web, need_library)

        ลำดับการตัดสินใจ:
          1. Keyword shortcut  → เร็วที่สุด (0 ms)
          2. Cache             → เร็ว (0 ms)
          3. Ollama LLM        → fallback (~300-800 ms)
        """
        msg = user_message.strip()

        # ── 1. Keyword shortcut ────────────────────────────────────────────
        has_web = bool(_WEB_PATTERNS.search(msg))
        has_lib = bool(_LIB_PATTERNS.search(msg))
        is_pure_chat = bool(_CHAT_PATTERNS.match(msg)) and not has_web and not has_lib

        if is_pure_chat:
            logger.debug("SmartRouter: keyword → CHAT")
            return False, False

        if has_web or has_lib:
            logger.info(f"SmartRouter: keyword → web={has_web} lib={has_lib}")
            return has_web, has_lib

        # ── 2. Cache ───────────────────────────────────────────────────────
        key = _cache_key(msg)
        cached = _cache_get(key)
        if cached is not None:
            logger.debug(f"SmartRouter: cache hit → web={cached[0]} lib={cached[1]}")
            return cached

        # ── 3. Ollama LLM ─────────────────────────────────────────────────
        result = await self._llm_decide(msg)
        _cache_set(key, result)
        return result

    async def _llm_decide(self, user_message: str) -> Tuple[bool, bool]:
        prompt = (
            f'วิเคราะห์คำถามต่อไปนี้แล้วตอบด้วยคีย์เวิร์ดเพียงบรรทัดเดียว\n'
            f'คำถาม: "{user_message}"\n\n'
            f'กฎ:\n'
            f'1. ถามเรื่องปัจจุบัน/ราคา/ข่าว/สภาพอากาศ → WEB\n'
            f'2. ถามเกี่ยวกับไฟล์/เอกสารที่เคยส่ง → LIB\n'
            f'3. ต้องใช้ทั้งสอง → WEB, LIB\n'
            f'4. ทักทาย/ความรู้ทั่วไป/เขียนโค้ด → CHAT\n'
            f'ตอบแค่คีย์เวิร์ด ห้ามมีคำอธิบาย'
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(f"{self.url}/api/chat", json={
                    "model":   self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream":  False,
                    "options": {"temperature": 0.0, "num_predict": 10, "num_ctx": 512},
                })
                r.raise_for_status()
                reply = r.json()["message"]["content"].strip().upper()

            need_web = "WEB" in reply
            need_lib = "LIB" in reply
            logger.info(f"SmartRouter: LLM → web={need_web} lib={need_lib} (raw: {reply})")
            return need_web, need_lib

        except Exception as e:
            logger.warning(f"SmartRouter LLM error (fallback to CHAT): {e}")
            return False, False


# Singleton
smart_router = SmartRouter()