# -*- coding: utf-8 -*-
"""
memory_manager.py — Conversation Memory System
- Rolling summary: สรุปบทสนทนาเก่าอัตโนมัติ
- Long-term memory: จำข้อมูลสำคัญข้ามวัน
- Context injection: ใส่ memory เข้า prompt อัตโนมัติ
"""
import json
import httpx
import logging
import os
from datetime import datetime
from typing import List, Dict, Optional
from database import db

logger = logging.getLogger(__name__)
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

SUMMARY_THRESHOLD = 20   # สรุปเมื่อ messages เกินจำนวนนี้
SUMMARY_KEEP      = 6    # เก็บ messages ล่าสุดไว้หลังสรุป
MAX_MEMORY_CHARS  = 800  # ขนาดสูงสุดของ memory summary


reasoning_framework = """
[คำสั่งการประมวลผล]
คุณต้องใช้กระบวนการคิดดังนี้ก่อนตอบเสมอ:
1. การวิเคราะห์ (Analysis): แยกแยะว่าผู้ใช้ต้องการอะไรกันแน่
2. การตรวจสอบ (Verification): หากมีข้อมูลจากไฟล์หรือเว็บ ให้ตรวจสอบว่าข้อมูลนั้นตอบคำถามได้จริงหรือไม่
3. การสังเคราะห์ (Synthesis): รวบรวมคำตอบที่ถูกต้องและเป็นปัจจุบันที่สุด
4. การกลั่นกรอง (Reflection): ตรวจสอบคำตอบสุดท้ายว่ามีส่วนไหนที่เดาเอาเองหรือไม่ (ถ้าไม่มีข้อมูลให้บอกตรงๆ ว่าไม่ทราบ)

ตอบเป็นภาษาไทยที่เป็นธรรมชาติ สุภาพ และใช้ Markdown เน้นจุดสำคัญได้ตามความเหมาะสม
"""

class MemoryManager:
    def __init__(self, ollama_base_url: str, model: str, system_prompt: str):
        self.ollama_url    = ollama_base_url
        self.model         = model
        self.system_prompt = system_prompt

    # ── Summary ────────────────────────────────────────────────────────────
    async def maybe_summarize(self, user_id: str, session_id: str,
                               messages: List[Dict]) -> List[Dict]:
        """
        ถ้า messages ยาวเกิน threshold → สรุปส่วนเก่าแล้วบีบอัด
        คืน messages ที่สั้นลงพร้อม memory summary
        """
        if len(messages) <= SUMMARY_THRESHOLD:
            return messages

        # แบ่ง: ส่วนเก่า (จะสรุป) + ส่วนใหม่ (เก็บไว้)
        old_msgs  = messages[:-SUMMARY_KEEP]
        keep_msgs = messages[-SUMMARY_KEEP:]

        logger.info(f"[{session_id}] Summarizing {len(old_msgs)} messages...")
        summary = await self._summarize_messages(old_msgs)

        if summary:
            # บันทึก summary ลง DB
            db.save_memory(user_id, session_id, "summary", summary)
            logger.info(f"[{session_id}] Summary saved: {len(summary)} chars")

        # คืน messages ที่บีบอัดแล้ว
        return keep_msgs

    async def _summarize_messages(self, messages: List[Dict]) -> str:
        """เรียก Ollama สรุปบทสนทนา"""
        conv_text = "\n".join([
            f"{'ผู้ใช้' if m['role']=='user' else 'AI'}: {m['content']}"
            for m in messages
        ])

        prompt = (
            f"สรุปบทสนทนาต่อไปนี้ให้กระชับ ครอบคลุมประเด็นสำคัญ "
            f"ข้อมูลที่ผู้ใช้แชร์ และสิ่งที่ตกลงกันไว้ ไม่เกิน 200 คำ:\n\n{conv_text}"
        )

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(f"{self.ollama_url}/api/chat", json={
                    "model":   self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream":  False,
                    "options": {"temperature": 0.3, "num_predict": 300, "num_ctx": OLLAMA_NUM_CTX},
                })
                r.raise_for_status()
                return r.json()["message"]["content"].strip()
        except Exception as e:
            logger.error(f"Summary error: {e}")
            return ""

    # ── Long-term Memory ───────────────────────────────────────────────────
    async def extract_key_info(self, user_id: str, message: str, reply: str):
        """
        วิเคราะห์บทสนทนาหาข้อมูลสำคัญที่ควรจำระยะยาว
        เช่น ชื่อ งาน ความชอบ เป้าหมาย
        """
        prompt = (
            f"จากบทสนทนานี้:\n"
            f"ผู้ใช้: {message}\n"
            f"AI: {reply}\n\n"
            f"จงทำหน้าที่เป็นระบบ Knowledge Graph สกัด 'ความจริง (Fact)' เกี่ยวกับผู้ใช้ออกมา\n"
            f"เช่น ชื่อ, อายุ, อาชีพ, ความชอบ, สิ่งที่ไม่ชอบ, งานที่กำลังทำ, หรือเป้าหมาย\n"
            f"กฎการสกัด:\n"
            f"1. สกัดเฉพาะข้อมูลที่เป็นความจริงและมีประโยชน์ในระยะยาวเท่านั้น\n"
            f"2. สกัดเป็นประโยคสั้นๆ กระชับ (เช่น 'ผู้ใช้ชื่อสมชาย', 'ผู้ใช้ชอบเขียน Python')\n"
            f"3. ถ้ามีข้อมูล ให้ตอบเป็น JSON: {{\"has_info\": true, \"info\": \"ความจริงที่สกัดได้\"}}\n"
            f"4. ถ้าเป็นแค่การทักทาย หรือคำถามทั่วไป ให้ตอบ: {{\"has_info\": false}}"
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(f"{self.ollama_url}/api/chat", json={
                    "model":   self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream":  False,
                    "options": {"temperature": 0.1, "num_predict": 150, "num_ctx": OLLAMA_NUM_CTX},
                })
                r.raise_for_status()
                text = r.json()["message"]["content"].strip()

                # parse JSON
                import re
                m = re.search(r'\{.*\}', text, re.DOTALL)
                if m:
                    data = json.loads(m.group())
                    if data.get("has_info") and data.get("info"):
                        db.save_memory(user_id, None, "fact", data["info"])
                        logger.info(f"[{user_id}] Key info saved: {data['info'][:50]}")
        except Exception as e:
            logger.debug(f"extract_key_info error: {e}")

    # ── Context Builder ────────────────────────────────────────────────────
    def build_context(self, user_id: str, session_id: str) -> str:
        """
        สร้าง context string จาก memory ทั้งหมด
        ใส่เข้า system prompt อัตโนมัติ
        """
        memories = db.get_memories(user_id, session_id)
        if not memories:
            return ""

        parts = []

        # Summary ของ session นี้
        summaries = [m for m in memories if m["type"] == "summary"]
        if summaries:
            latest = summaries[-1]["content"]
            parts.append(f"[สรุปบทสนทนาก่อนหน้า]\n{latest}")

        # Long-term facts
        facts = [m for m in memories if m["type"] == "fact"]
        if facts:
            fact_text = "\n".join(f"- {f['content']}" for f in facts[-5:])
            parts.append(f"[ข้อมูลที่รู้เกี่ยวกับผู้ใช้]\n{fact_text}")

        if not parts:
            return ""

        context = "\n\n".join(parts)
        if len(context) > MAX_MEMORY_CHARS:
            context = context[:MAX_MEMORY_CHARS] + "..."

        return f"\n\n{reasoning_framework}\n---\n{context}\n---"

    def get_enhanced_system_prompt(self, user_id: str, session_id: str) -> str:
        """System prompt + memory context"""
        context = self.build_context(user_id, session_id)
        return self.system_prompt + context
