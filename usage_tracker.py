# -*- coding: utf-8 -*-
import logging
from datetime import datetime
from typing import Dict, List, Optional
from database import db

logger = logging.getLogger(__name__)

class UsageTracker:
    def __init__(self):
        pass

    def record_usage(self, session_id: str, prompt_tokens: int, completion_tokens: int):
        """
        บันทึกการใช้งาน token ลงใน message ล่าสุดของ session นั้น
        """
        try:
            conn = db._conn()
            # หามิสเสจล่าสุดของเซสชันนี้ที่เพิ่งบันทึกไป (ซึ่งเป็นของ bot)
            row = conn.execute("""
                SELECT id FROM messages 
                WHERE session_id = ? 
                ORDER BY id DESC LIMIT 1
            """, (session_id,)).fetchone()
            
            if row:
                message_id = row['id']
                conn.execute("""
                    UPDATE messages 
                    SET prompt_tokens = ?, completion_tokens = ? 
                    WHERE id = ?
                """, (prompt_tokens, completion_tokens, message_id))
                conn.commit()
                logger.debug(f"Recorded usage for msg {message_id}: {prompt_tokens} in, {completion_tokens} out")
        except Exception as e:
            logger.error(f"Error recording usage: {e}")

    def get_user_stats(self, user_id: str) -> Dict:
        """
        ดึงสถิติการใช้งานรวมของผู้ใช้
        """
        try:
            conn = db._conn()
            row = conn.execute("""
                SELECT 
                    SUM(m.prompt_tokens) as total_input,
                    SUM(m.completion_tokens) as total_output,
                    COUNT(m.id) as total_messages
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE s.user_id = ?
            """, (user_id,)).fetchone()
            
            return {
                "user_id": user_id,
                "input_tokens": row['total_input'] or 0,
                "output_tokens": row['total_output'] or 0,
                "total_tokens": (row['total_input'] or 0) + (row['total_output'] or 0),
                "messages": row['total_messages'] or 0
            }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {"error": str(e)}

    def get_all_usage(self) -> List[Dict]:
        """
        ดึงสถิติการใช้งานของผู้ใช้ทุกคน (สำหรับ Admin)
        """
        try:
            conn = db._conn()
            rows = conn.execute("""
                SELECT 
                    s.user_id,
                    u.display_name,
                    SUM(m.prompt_tokens) as input,
                    SUM(m.completion_tokens) as output,
                    COUNT(m.id) as msgs
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                LEFT JOIN users u ON s.user_id = u.id
                GROUP BY s.user_id
                ORDER BY (SUM(m.prompt_tokens) + SUM(m.completion_tokens)) DESC
            """).fetchall()
            
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error getting all usage: {e}")
            return []

# Singleton instance
usage_tracker = UsageTracker()
