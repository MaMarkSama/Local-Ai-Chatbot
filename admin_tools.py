# -*- coding: utf-8 -*-
import hashlib
import hmac
import os
from typing import Dict

import httpx

from database import db


SYNC_ENDPOINT = os.getenv("MYSQL_SYNC_ENDPOINT", "").strip()
SYNC_TOKEN = os.getenv("SYNC_API_TOKEN", "").strip()


def snapshot_signature(body: bytes, token: str = SYNC_TOKEN) -> str:
    return hmac.new(token.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def push_sqlite_snapshot(limit: int = 5000) -> Dict:
    if not SYNC_ENDPOINT:
        return {"ok": False, "error": "MYSQL_SYNC_ENDPOINT is not configured"}
    if not SYNC_TOKEN:
        return {"ok": False, "error": "SYNC_API_TOKEN is not configured"}

    payload = db.export_snapshot(limit=limit)
    import json
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Sync-Token": SYNC_TOKEN,
        "X-Signature": snapshot_signature(body),
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(SYNC_ENDPOINT, content=body, headers=headers)
        response.raise_for_status()
        return response.json()

