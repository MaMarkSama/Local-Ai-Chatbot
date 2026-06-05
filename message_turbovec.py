# -*- coding: utf-8 -*-
"""
message_turbovec.py — Semantic RAG Engine
รับ DocResult จาก file_processor แล้วทำ:
  1. Structure-aware chunking  — chunk ตาม heading boundary + overlap
  2. Metadata injection        — ทุก chunk รู้ว่าตัวเองอยู่ใต้หัวข้อไหน
  3. Vector indexing           — nomic-embed-text / fallback hash
  4. Re-ranked retrieval       — cosine + heading_boost + position_boost
"""
import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Any

import httpx

DEFAULT_DB_PATH = os.getenv("DB_PATH", "chat_history.db")


# ══════════════════════════════════════════════════════════════════════════════
# Vector math helpers
# ══════════════════════════════════════════════════════════════════════════════

def _normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[\w\u0E00-\u0E7F]+", text.lower(), flags=re.UNICODE)
    return tokens if tokens else [text[i:i + 3].lower() for i in range(max(0, len(text) - 2))]


def fallback_embedding(text: str, dims: int = 384) -> List[float]:
    vec = [0.0] * dims
    for token in _tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8", errors="ignore"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[bucket] += sign
    return _normalize(vec)


# ══════════════════════════════════════════════════════════════════════════════
# Structure-aware chunker
# ══════════════════════════════════════════════════════════════════════════════

def make_chunks_from_blocks(
    blocks: List[Dict[str, Any]],
    chunk_chars: int = 1200,
    overlap_chars: int = 200,
) -> List[Dict[str, Any]]:
    """
    แปลง DocResult["blocks"] → List[chunk]

    แต่ละ chunk มี:
      text         — เนื้อหา (พร้อม metadata header)
      heading_path — breadcrumb ของ heading ที่ครอบอยู่
      block_ids    — list ของ block id ที่อยู่ใน chunk นี้
      chunk_type   — "semantic" | "table" | "code"
      page         — หน้าแรกของ chunk (ถ้ามี)
    """
    if not blocks:
        return []

    chunks: List[Dict[str, Any]] = []

    # ── Table และ Code blocks: ไม่แบ่ง ส่งตัวต่อตัวแต่ตัดถ้ายาวเกิน ──────────
    # ── Heading + Paragraph: รวม blocks จนถึง chunk_chars แล้วตัด ────────────

    # กลุ่ม block ตาม heading section
    sections: List[Dict] = []
    current_section: Dict = {"heading_path": "", "heading_text": "", "blocks": []}

    for block in blocks:
        btype = block.get("type", "paragraph")

        if btype == "heading":
            # บันทึก section เดิม
            if current_section["blocks"]:
                sections.append(current_section)
            current_section = {
                "heading_path": block.get("heading_path", ""),
                "heading_text": block.get("text", ""),
                "blocks": [],
            }
        elif btype in ("table", "code"):
            # flush section ก่อน
            if current_section["blocks"]:
                sections.append(current_section)
                current_section = {**current_section, "blocks": []}
            # table/code = section เดียว
            sections.append({
                "heading_path": block.get("heading_path", ""),
                "heading_text": "",
                "blocks": [block],
                "_force_type": btype,
            })
        else:
            current_section["blocks"].append(block)

    if current_section["blocks"]:
        sections.append(current_section)

    chunk_id = 0
    for sec in sections:
        forced_type = sec.get("_force_type")
        heading_path = sec["heading_path"]
        heading_text = sec.get("heading_text", "")

        if forced_type in ("table", "code"):
            # ส่ง block เดียว ตัดถ้ายาวเกิน 3x chunk_chars
            block = sec["blocks"][0]
            raw_text = block.get("text", "")
            max_block = chunk_chars * 3
            if len(raw_text) > max_block:
                # แบ่งเป็น sub-chunks
                step = chunk_chars - overlap_chars
                for start in range(0, len(raw_text), max(1, step)):
                    sub = raw_text[start:start + chunk_chars].strip()
                    if not sub:
                        continue
                    header = _make_header(heading_path, block.get("page"), forced_type, start > 0)
                    chunks.append({
                        "id": chunk_id,
                        "text": header + sub,
                        "heading_path": heading_path,
                        "block_ids": [block.get("id", 0)],
                        "chunk_type": forced_type,
                        "page": block.get("page"),
                    })
                    chunk_id += 1
            else:
                header = _make_header(heading_path, block.get("page"), forced_type, False)
                chunks.append({
                    "id": chunk_id,
                    "text": header + raw_text,
                    "heading_path": heading_path,
                    "block_ids": [block.get("id", 0)],
                    "chunk_type": forced_type,
                    "page": block.get("page"),
                })
                chunk_id += 1
            continue

        # ── Paragraph / Meta blocks: sliding window ─────────────────────────
        sec_blocks = sec["blocks"]
        if not sec_blocks:
            continue

        accumulated = ""
        acc_blocks: List[int] = []
        first_page = sec_blocks[0].get("page")
        overlap_buf = ""  # tail ของ chunk ก่อนสำหรับ overlap

        for block in sec_blocks:
            raw = block.get("text", "").strip()
            if not raw:
                continue

            candidate = (accumulated + "\n\n" + raw).strip() if accumulated else raw

            if len(candidate) <= chunk_chars:
                accumulated = candidate
                acc_blocks.append(block.get("id", 0))
            else:
                # flush ก่อน
                if accumulated:
                    header = _make_header(heading_path, first_page, "semantic", False)
                    chunk_text = header + (overlap_buf + "\n\n" + accumulated).strip() if overlap_buf else header + accumulated
                    chunks.append({
                        "id": chunk_id,
                        "text": chunk_text[:chunk_chars + len(header) + overlap_chars],
                        "heading_path": heading_path,
                        "block_ids": acc_blocks[:],
                        "chunk_type": "semantic",
                        "page": first_page,
                    })
                    chunk_id += 1
                    # เก็บ tail สำหรับ overlap ครั้งถัดไป
                    overlap_buf = accumulated[-overlap_chars:] if len(accumulated) > overlap_chars else accumulated

                # raw ยาวกว่า chunk_chars → ตัดเป็น sub-chunks
                if len(raw) > chunk_chars:
                    step = max(1, chunk_chars - overlap_chars)
                    for start in range(0, len(raw), step):
                        sub = raw[start:start + chunk_chars].strip()
                        if not sub:
                            continue
                        header = _make_header(heading_path, block.get("page"), "semantic", start > 0)
                        chunks.append({
                            "id": chunk_id,
                            "text": header + sub,
                            "heading_path": heading_path,
                            "block_ids": [block.get("id", 0)],
                            "chunk_type": "semantic",
                            "page": block.get("page"),
                        })
                        chunk_id += 1
                    accumulated = ""
                    acc_blocks = []
                    overlap_buf = raw[-overlap_chars:]
                else:
                    accumulated = raw
                    acc_blocks = [block.get("id", 0)]
                    first_page = block.get("page")

        if accumulated:
            header = _make_header(heading_path, first_page, "semantic", False)
            chunk_text = (overlap_buf + "\n\n" + accumulated).strip() if overlap_buf else accumulated
            chunks.append({
                "id": chunk_id,
                "text": header + chunk_text,
                "heading_path": heading_path,
                "block_ids": acc_blocks,
                "chunk_type": "semantic",
                "page": first_page,
            })
            chunk_id += 1

    return chunks


def _make_header(heading_path: str, page: Optional[int],
                 chunk_type: str, is_continuation: bool) -> str:
    """สร้าง metadata header ที่ inject เข้าต้น chunk เพื่อให้ embedding รู้ context"""
    parts = []
    if heading_path:
        parts.append(f"[หัวข้อ: {heading_path}]")
    if page:
        parts.append(f"[หน้า {page}]")
    if chunk_type in ("table", "code"):
        parts.append(f"[{chunk_type.upper()}]")
    if is_continuation:
        parts.append("[ต่อ]")
    return (" ".join(parts) + "\n") if parts else ""


# ── Legacy plain-text splitter (fallback สำหรับ content string) ─────────────

def split_text(text: str, max_chars: int = 1200, overlap: int = 160) -> List[str]:
    clean = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not clean:
        return []
    parts = re.split(r"\n\n+|(?<=[.!?。！？])\s+", clean)
    chunks: List[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > max_chars:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            step = max(1, max_chars - overlap)
            for start in range(0, len(part), step):
                chunk = part[start:start + max_chars].strip()
                if chunk:
                    chunks.append(chunk)
            continue
        if len(current) + len(part) <= max_chars:
            current = f"{current}\n\n{part}".strip() if current else part
            continue
        if current.strip():
            chunks.append(current.strip())
        current = part
    if current.strip():
        chunks.append(current.strip())
    return chunks


# ══════════════════════════════════════════════════════════════════════════════
# MessageTurboVec
# ══════════════════════════════════════════════════════════════════════════════

class MessageTurboVec:
    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        ollama_base_url: str = "http://localhost:11434",
        embed_model: str = "nomic-embed-text",
        enabled: bool = True,
    ):
        self.db_path = db_path
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.embed_model = embed_model
        self.enabled = enabled
        self._remote_embeddings = enabled
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS document_vectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_text TEXT NOT NULL,
                    heading_path TEXT NOT NULL DEFAULT '',
                    chunk_type TEXT NOT NULL DEFAULT 'semantic',
                    page INTEGER,
                    vector_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, session_id, file_hash, chunk_index)
                );
                CREATE INDEX IF NOT EXISTS idx_doc_vectors_lookup
                    ON document_vectors(user_id, session_id, file_hash);
            """)
            # migrate: add new columns if missing
            for col, definition in [
                ("heading_path", "TEXT NOT NULL DEFAULT ''"),
                ("chunk_type",   "TEXT NOT NULL DEFAULT 'semantic'"),
                ("page",         "INTEGER"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE document_vectors ADD COLUMN {col} {definition}")
                except Exception:
                    pass
            conn.commit()
        finally:
            conn.close()

    # ── Embedding ────────────────────────────────────────────────────────────

    async def embed(self, text: str) -> List[float]:
        if not self.enabled or not self._remote_embeddings:
            return fallback_embedding(text)
        payload = {"model": self.embed_model, "input": text}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.post(f"{self.ollama_base_url}/api/embed", json=payload)
                r.raise_for_status()
                embeddings = r.json().get("embeddings") or []
                if embeddings:
                    return _normalize([float(v) for v in embeddings[0]])
        except Exception:
            pass
        # legacy endpoint
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.post(f"{self.ollama_base_url}/api/embeddings",
                                      json={"model": self.embed_model, "prompt": text})
                r.raise_for_status()
                embedding = r.json().get("embedding") or []
                if embedding:
                    return _normalize([float(v) for v in embedding])
        except Exception:
            self._remote_embeddings = False
        return fallback_embedding(text)

    # ── Hashing ──────────────────────────────────────────────────────────────

    def file_hash(self, filename: str, content: str) -> str:
        h = hashlib.sha256()
        h.update(filename.encode("utf-8", errors="ignore"))
        h.update(b"\0")
        h.update(content.encode("utf-8", errors="ignore"))
        return h.hexdigest()

    def has_index(self, user_id: str, session_id: str, file_hash: str) -> bool:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM document_vectors WHERE user_id=? AND session_id=? AND file_hash=? LIMIT 1",
                (user_id, session_id, file_hash),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    # ── Indexing ─────────────────────────────────────────────────────────────

    async def index_document(
        self,
        user_id: str,
        session_id: str,
        filename: str,
        content: str,
        chunk_chars: int = 1200,
        overlap: int = 160,
        doc_result: Optional[Dict] = None,
    ) -> Dict:
        """
        ถ้ามี doc_result (DocResult จาก file_processor) → ใช้ structure-aware chunking
        ถ้าไม่มี → fallback ไป split_text เดิม
        """
        file_hash = self.file_hash(filename, content)
        if self.has_index(user_id, session_id, file_hash):
            return {"file_hash": file_hash, "chunks": 0, "reused": True}

        # เลือก chunking strategy
        if doc_result and doc_result.get("blocks"):
            chunks_raw = make_chunks_from_blocks(
                doc_result["blocks"],
                chunk_chars=chunk_chars,
                overlap_chars=overlap,
            )
            chunk_texts     = [c["text"] for c in chunks_raw]
            chunk_metadata  = chunks_raw
        else:
            # legacy: plain text split
            chunk_texts    = split_text(content, max_chars=chunk_chars, overlap=overlap)
            chunk_metadata = [
                {"id": i, "text": t, "heading_path": "", "chunk_type": "semantic", "page": None}
                for i, t in enumerate(chunk_texts)
            ]

        if not chunk_texts:
            return {"file_hash": file_hash, "chunks": 0, "reused": False}

        import asyncio
        sem = asyncio.Semaphore(4)

        async def _embed_with_sem(idx: int, text: str):
            async with sem:
                vec = await self.embed(text)
                return idx, vec

        tasks = [_embed_with_sem(i, t) for i, t in enumerate(chunk_texts)]
        results = await asyncio.gather(*tasks)

        now = datetime.now().isoformat()
        rows = []
        for idx, vector in results:
            meta = chunk_metadata[idx]
            rows.append((
                user_id, session_id, filename, file_hash,
                idx,
                meta["text"],
                meta.get("heading_path", ""),
                meta.get("chunk_type", "semantic"),
                meta.get("page"),
                json.dumps(vector, separators=(",", ":")),
                now,
            ))

        conn = self._conn()
        try:
            conn.executemany(
                """
                INSERT OR REPLACE INTO document_vectors
                (user_id, session_id, filename, file_hash, chunk_index,
                 chunk_text, heading_path, chunk_type, page, vector_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

        return {"file_hash": file_hash, "chunks": len(rows), "reused": False}

    # ── Retrieval with re-ranking ─────────────────────────────────────────────

    async def search(
        self,
        user_id: str,
        session_id: Optional[str],
        query: str,
        file_hash: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict]:
        query_vector = await self.embed(query)
        query_lower  = query.lower()

        sql = """
            SELECT filename, file_hash, chunk_index, chunk_text,
                   heading_path, chunk_type, page, vector_json
            FROM document_vectors
            WHERE user_id = ?
        """
        args: List = [user_id]
        if session_id:
            sql += " AND session_id = ?"
            args.append(session_id)
        if file_hash:
            sql += " AND file_hash = ?"
            args.append(file_hash)

        conn = self._conn()
        try:
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()

        total = len(rows)
        scored = []
        for row in rows:
            vector = json.loads(row["vector_json"])
            cos_score = _cosine(query_vector, vector)

            # ── Re-ranking boosts ────────────────────────────────────────
            # 1. heading match boost: ถ้า heading_path มีคำจาก query
            heading = (row["heading_path"] or "").lower()
            heading_boost = 0.0
            query_tokens = _tokenize(query_lower)
            matching = sum(1 for t in query_tokens if t in heading)
            if matching:
                heading_boost = min(0.15, 0.05 * matching)

            # 2. keyword boost: คำจาก query ปรากฏใน chunk text
            chunk_lower = row["chunk_text"].lower()
            kw_matching = sum(1 for t in query_tokens if t in chunk_lower)
            kw_boost = min(0.10, 0.02 * kw_matching)

            # 3. table/code type boost เมื่อ query ถามเกี่ยวกับตาราง/code
            type_boost = 0.0
            chunk_type = row["chunk_type"]
            if chunk_type == "table" and any(
                w in query_lower for w in ("ตาราง", "แถว", "คอลัมน์", "ข้อมูล", "table", "row", "column")
            ):
                type_boost = 0.05
            if chunk_type == "code" and any(
                w in query_lower for w in ("โค้ด", "ฟังก์ชัน", "function", "class", "def", "code")
            ):
                type_boost = 0.05

            final_score = cos_score + heading_boost + kw_boost + type_boost

            scored.append({
                "filename":     row["filename"],
                "file_hash":    row["file_hash"],
                "chunk_index":  row["chunk_index"],
                "content":      row["chunk_text"],
                "heading_path": row["heading_path"],
                "chunk_type":   chunk_type,
                "page":         row["page"],
                "score":        round(final_score, 4),
                "cos_score":    round(cos_score, 4),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)

        # diversity filter: ไม่เอา chunks ติดกัน > 2 จาก heading path เดียวกัน
        seen_paths: Dict[str, int] = {}
        diverse = []
        for item in scored:
            path = item["heading_path"] or "_root_"
            count = seen_paths.get(path, 0)
            if count < 2:
                diverse.append(item)
                seen_paths[path] = count + 1
            if len(diverse) >= top_k:
                break

        return diverse