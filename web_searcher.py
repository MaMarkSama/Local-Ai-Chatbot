# -*- coding: utf-8 -*-
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from duckduckgo_search import DDGS
from typing import List, Dict

logger = logging.getLogger(__name__)

# thread pool สำหรับรัน blocking DuckDuckGo ใน thread แยก
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ddg_search")


class WebSearcher:
    def __init__(self, max_results: int = 5):
        self.max_results = max_results

    # ── sync helper (รันใน thread pool) ───────────────────────────────────
    def _search_sync(self, query: str) -> str:
        logger.info(f"Searching web for: {query}")
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(
                    query,
                    region="th-th",
                    safesearch="off",
                    max_results=self.max_results,
                ))

            # fallback: ค้นหาแบบ global ถ้า TH ไม่เจอ
            if not results:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=self.max_results))

            if not results:
                logger.warning(f"No results for: {query}")
                return ""

            lines = []
            for i, r in enumerate(results, 1):
                title   = r.get("title", "No Title")
                snippet = r.get("body",  "No Description")
                link    = r.get("href",  "#")
                lines.append(f"[{i}] {title}\nURL: {link}\nเนื้อหา: {snippet}")
            return "\n\n".join(lines)

        except Exception as e:
            logger.error(f"Web search error: {e}")
            return f"(เกิดข้อผิดพลาดในการค้นหาเว็บ: {e})"

    # ── async wrapper (ไม่ block event loop) ─────────────────────────────
    async def search(self, query: str) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, self._search_sync, query)

    # backward-compat: เผื่อโค้ดอื่นยังเรียก .search_sync() ตรงๆ
    def search_sync(self, query: str) -> str:
        return self._search_sync(query)


# Singleton instance
web_searcher = WebSearcher()