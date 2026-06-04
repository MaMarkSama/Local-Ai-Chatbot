# -*- coding: utf-8 -*-
import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

import httpx


DEFAULT_DB_PATH = os.getenv("DB_PATH", "chat_history.db")


def _normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if not norm:
        return vec
    return [v / norm for v in vec]


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[\w\u0E00-\u0E7F]+", text.lower(), flags=re.UNICODE)
    if tokens:
        return tokens
    return [text[i:i + 3].lower() for i in range(max(0, len(text) - 2))]


def fallback_embedding(text: str, dims: int = 384) -> List[float]:
    vec = [0.0] * dims
    for token in _tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8", errors="ignore"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[bucket] += sign
    return _normalize(vec)


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
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS document_vectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_text TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, session_id, file_hash, chunk_index)
                );
                CREATE INDEX IF NOT EXISTS idx_doc_vectors_lookup
                    ON document_vectors(user_id, session_id, file_hash);
                """
            )
            conn.commit()
        finally:
            conn.close()

    async def embed(self, text: str) -> List[float]:
        if not self.enabled or not self._remote_embeddings:
            return fallback_embedding(text)

        payload = {"model": self.embed_model, "input": text}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.post(f"{self.ollama_base_url}/api/embed", json=payload)
                response.raise_for_status()
                data = response.json()
                embeddings = data.get("embeddings") or []
                if embeddings:
                    return _normalize([float(v) for v in embeddings[0]])
        except Exception:
            pass

        legacy_payload = {"model": self.embed_model, "prompt": text}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.post(f"{self.ollama_base_url}/api/embeddings", json=legacy_payload)
                response.raise_for_status()
                data = response.json()
                embedding = data.get("embedding") or []
                if embedding:
                    return _normalize([float(v) for v in embedding])
        except Exception:
            self._remote_embeddings = False

        return fallback_embedding(text)

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
                """
                SELECT 1 FROM document_vectors
                WHERE user_id = ? AND session_id = ? AND file_hash = ?
                LIMIT 1
                """,
                (user_id, session_id, file_hash),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    async def index_document(
        self,
        user_id: str,
        session_id: str,
        filename: str,
        content: str,
        chunk_chars: int = 1200,
        overlap: int = 160,
    ) -> Dict:
        file_hash = self.file_hash(filename, content)
        chunks = split_text(content, max_chars=chunk_chars, overlap=overlap)
        if not chunks:
            return {"file_hash": file_hash, "chunks": 0, "reused": False}

        if self.has_index(user_id, session_id, file_hash):
            return {"file_hash": file_hash, "chunks": len(chunks), "reused": True}

        now = datetime.now().isoformat()
        rows = []
        for index, chunk in enumerate(chunks):
            vector = await self.embed(chunk)
            rows.append((
                user_id,
                session_id,
                filename,
                file_hash,
                index,
                chunk,
                json.dumps(vector, separators=(",", ":")),
                now,
            ))

        conn = self._conn()
        try:
            conn.executemany(
                """
                INSERT OR REPLACE INTO document_vectors
                (user_id, session_id, filename, file_hash, chunk_index, chunk_text, vector_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

        return {"file_hash": file_hash, "chunks": len(chunks), "reused": False}

    async def search(
        self,
        user_id: str,
        session_id: str,
        query: str,
        file_hash: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict]:
        query_vector = await self.embed(query)
        sql = """
            SELECT filename, file_hash, chunk_index, chunk_text, vector_json
            FROM document_vectors
            WHERE user_id = ? AND session_id = ?
        """
        args: List[str] = [user_id, session_id]
        if file_hash:
            sql += " AND file_hash = ?"
            args.append(file_hash)

        conn = self._conn()
        try:
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()

        scored = []
        for row in rows:
            vector = json.loads(row["vector_json"])
            scored.append({
                "filename": row["filename"],
                "file_hash": row["file_hash"],
                "chunk_index": row["chunk_index"],
                "content": row["chunk_text"],
                "score": _cosine(query_vector, vector),
            })

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]
