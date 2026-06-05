# -*- coding: utf-8 -*-
"""
file_processor.py — Document Intelligence Parser
แทนที่การ extract ข้อความดิบด้วย structured document parsing

Output format (DocResult):
  {
    "type": "structured" | "image",
    "format": "pdf" | "docx" | "xlsx" | "csv" | "code" | "text" | ...,
    "title": str,          # ชื่อเอกสาร / sheet แรก
    "summary": str,        # สั้นๆ สำหรับแสดงใน UI
    "blocks": [            # semantic blocks — ส่งให้ chunker
      {
        "id": int,
        "type": "heading" | "paragraph" | "table" | "code" | "list" | "meta",
        "level": int,      # heading level (1-6), 0 = ไม่ใช่ heading
        "heading_path": str,  # breadcrumb เช่น "บทที่ 1 > 1.1 > 1.1.2"
        "text": str,
        "page": int | None,
        "row_start": int | None,  # สำหรับ table/csv
      }
    ],
    # legacy: ใช้สำหรับ fallback และ short-doc path
    "content": str,        # full text รวมทั้งหมด (ไม่ตัด)
    "page_count": int,
  }
"""
import io
import base64
import csv
import re
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Tuple, Optional

# ── Type alias ──────────────────────────────────────────────────────────────
Block = Dict[str, Any]
DocResult = Dict[str, Any]


# ══════════════════════════════════════════════════════════════════════════════
# PDF
# ══════════════════════════════════════════════════════════════════════════════

def _is_heading_line(line: str) -> Tuple[bool, int]:
    """
    ตรวจว่าบรรทัดนี้เป็น heading หรือไม่ และระดับเท่าใด
    ใช้ heuristic: สั้น, ไม่มี punctuation ท้าย, มีรูปแบบตัวเลข/หัวข้อ
    """
    line = line.strip()
    if not line or len(line) > 120:
        return False, 0

    # รูปแบบตัวเลข: 1. / 1.1 / 1.1.1 / บทที่ 1
    num_patterns = [
        (r"^(บทที่|หัวข้อ|ส่วนที่|Chapter|Section|Part)\s*\d+", 1),
        (r"^\d+\.\s+\S", 2),
        (r"^\d+\.\d+\s+\S", 3),
        (r"^\d+\.\d+\.\d+\s+\S", 4),
    ]
    for pattern, level in num_patterns:
        if re.match(pattern, line, re.IGNORECASE):
            return True, level

    # ALL CAPS สั้นๆ (ภาษาอังกฤษ)
    if line.isupper() and 3 <= len(line) <= 80 and not line.endswith(('.', ',', ';')):
        return True, 2

    # ตัวหนาจาก font size heuristic (ใช้ความยาวและการไม่มี stop-word ท้าย)
    if (len(line) <= 60
            and not line.endswith(('.', ',', ':', ';', ')', '\"', '\''))
            and sum(1 for c in line if c.isalpha()) > len(line) * 0.5):
        return True, 3

    return False, 0


def process_pdf(data: bytes) -> DocResult:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(data))
    total_pages = len(reader.pages)

    blocks: List[Block] = []
    block_id = 0
    full_lines: List[str] = []

    heading_stack: List[str] = []  # breadcrumb

    for page_num, page in enumerate(reader.pages, 1):
        raw = (page.extract_text() or "").strip()
        if not raw:
            continue

        lines = raw.splitlines()
        current_para: List[str] = []
        current_page = page_num

        def flush_para():
            nonlocal block_id
            text = " ".join(current_para).strip()
            if text:
                blocks.append({
                    "id": block_id,
                    "type": "paragraph",
                    "level": 0,
                    "heading_path": " > ".join(heading_stack) if heading_stack else "",
                    "text": text,
                    "page": current_page,
                    "row_start": None,
                })
                block_id += 1
                full_lines.append(text)
            current_para.clear()

        for line in lines:
            stripped = line.strip()
            if not stripped:
                flush_para()
                continue

            is_h, level = _is_heading_line(stripped)
            if is_h:
                flush_para()
                # อัปเดต breadcrumb
                while len(heading_stack) >= level:
                    if heading_stack:
                        heading_stack.pop()
                    else:
                        break
                heading_stack.append(stripped)

                blocks.append({
                    "id": block_id,
                    "type": "heading",
                    "level": level,
                    "heading_path": " > ".join(heading_stack),
                    "text": stripped,
                    "page": page_num,
                    "row_start": None,
                })
                block_id += 1
                full_lines.append(f"\n## {stripped}")
            else:
                current_para.append(stripped)

        flush_para()

    content = "\n".join(full_lines)
    return {
        "type": "structured",
        "format": "pdf",
        "title": "",
        "summary": f"PDF {total_pages} หน้า ({len(blocks)} blocks)",
        "blocks": blocks,
        "content": content,
        "page_count": total_pages,
    }


# ══════════════════════════════════════════════════════════════════════════════
# DOCX
# ══════════════════════════════════════════════════════════════════════════════

def process_docx(data: bytes) -> DocResult:
    from docx import Document
    doc = Document(io.BytesIO(data))

    blocks: List[Block] = []
    block_id = 0
    full_lines: List[str] = []
    heading_stack: List[str] = []

    def heading_level(style_name: str) -> int:
        m = re.search(r"(\d+)$", style_name)
        return int(m.group(1)) if m else 1

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        is_heading = para.style.name.startswith("Heading")
        if is_heading:
            level = heading_level(para.style.name)
            while len(heading_stack) >= level:
                if heading_stack:
                    heading_stack.pop()
                else:
                    break
            heading_stack.append(text)

            blocks.append({
                "id": block_id,
                "type": "heading",
                "level": level,
                "heading_path": " > ".join(heading_stack),
                "text": text,
                "page": None,
                "row_start": None,
            })
            full_lines.append(f"\n{'#' * level} {text}")
        else:
            blocks.append({
                "id": block_id,
                "type": "paragraph",
                "level": 0,
                "heading_path": " > ".join(heading_stack) if heading_stack else "",
                "text": text,
                "page": None,
                "row_start": None,
            })
            full_lines.append(text)
        block_id += 1

    # Tables
    for table in doc.tables:
        rows_text = []
        for row in table.rows:
            row_cells = [c.text.strip() for c in row.cells]
            rows_text.append(" | ".join(row_cells))

        table_text = "\n".join(rows_text)
        blocks.append({
            "id": block_id,
            "type": "table",
            "level": 0,
            "heading_path": " > ".join(heading_stack) if heading_stack else "",
            "text": table_text,
            "page": None,
            "row_start": None,
        })
        full_lines.append(f"\n[ตาราง]\n{table_text}")
        block_id += 1

    content = "\n".join(full_lines)
    return {
        "type": "structured",
        "format": "docx",
        "title": "",
        "summary": "Word Document",
        "blocks": blocks,
        "content": content,
        "page_count": None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CSV
# ══════════════════════════════════════════════════════════════════════════════

def process_csv(data: bytes) -> DocResult:
    for enc in ("utf-8-sig", "tis-620", "windows-874"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    headers = list(reader.fieldnames or [])
    total = len(rows)

    blocks: List[Block] = []
    ROWS_PER_BLOCK = 30  # แต่ละ block = 30 แถว พร้อม header

    # Meta block
    blocks.append({
        "id": 0,
        "type": "meta",
        "level": 0,
        "heading_path": "",
        "text": f"CSV — {total} แถว, {len(headers)} คอลัมน์\nคอลัมน์: {', '.join(headers)}",
        "page": None,
        "row_start": 0,
    })

    for chunk_idx, start in enumerate(range(0, total, ROWS_PER_BLOCK), 1):
        chunk_rows = rows[start:start + ROWS_PER_BLOCK]
        lines = [f"คอลัมน์: {' | '.join(headers)}"]
        for i, row in enumerate(chunk_rows, start + 1):
            lines.append(f"แถว {i}: " + " | ".join(f"{k}={v}" for k, v in row.items()))
        blocks.append({
            "id": chunk_idx,
            "type": "table",
            "level": 0,
            "heading_path": f"แถว {start+1}–{start+len(chunk_rows)}",
            "text": "\n".join(lines),
            "page": None,
            "row_start": start + 1,
        })

    content = "\n".join(b["text"] for b in blocks)
    return {
        "type": "structured",
        "format": "csv",
        "title": "",
        "summary": f"CSV {total} แถว",
        "blocks": blocks,
        "content": content,
        "page_count": None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# XLSX
# ══════════════════════════════════════════════════════════════════════════════

def process_xlsx(data: bytes) -> DocResult:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)

    blocks: List[Block] = []
    block_id = 0
    full_lines: List[str] = []
    ROWS_PER_BLOCK = 30

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        all_rows = list(ws.iter_rows(values_only=True))
        if not all_rows:
            continue

        # Sheet meta block
        blocks.append({
            "id": block_id,
            "type": "heading",
            "level": 1,
            "heading_path": sheet_name,
            "text": f"Sheet: {sheet_name} ({len(all_rows)} แถว)",
            "page": None,
            "row_start": None,
        })
        full_lines.append(f"\n## Sheet: {sheet_name}")
        block_id += 1

        # ใช้แถวแรกเป็น header
        header_row = all_rows[0]
        headers = [str(c) if c is not None else f"Col{i+1}" for i, c in enumerate(header_row)]
        data_rows = all_rows[1:]

        for start in range(0, len(data_rows), ROWS_PER_BLOCK):
            chunk = data_rows[start:start + ROWS_PER_BLOCK]
            lines = [f"คอลัมน์: {' | '.join(headers)}"]
            for i, row in enumerate(chunk, start + 2):
                lines.append(f"แถว {i}: " + " | ".join(
                    str(c) if c is not None else "" for c in row))
            block_text = "\n".join(lines)
            blocks.append({
                "id": block_id,
                "type": "table",
                "level": 0,
                "heading_path": sheet_name,
                "text": block_text,
                "page": None,
                "row_start": start + 2,
            })
            full_lines.append(block_text)
            block_id += 1

    content = "\n".join(full_lines)
    return {
        "type": "structured",
        "format": "xlsx",
        "title": wb.sheetnames[0] if wb.sheetnames else "",
        "summary": f"Excel {len(wb.sheetnames)} sheet",
        "blocks": blocks,
        "content": content,
        "page_count": None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Code / Text / XML
# ══════════════════════════════════════════════════════════════════════════════

def process_code(data: bytes, ext: str) -> DocResult:
    """
    สำหรับ .py .js .ts .java .cpp .c .go .rs และภาษาอื่นๆ
    แบ่ง chunk ตาม function/class boundary
    """
    text = data.decode("utf-8-sig", errors="replace")

    # Pattern ตาม function/class/method declaration
    boundary_pattern = re.compile(
        r"^(class |def |function |async function |func |pub fn |fn |"
        r"public |private |protected |static |export )",
        re.MULTILINE,
    )

    blocks: List[Block] = []
    block_id = 0
    positions = [m.start() for m in boundary_pattern.finditer(text)]
    positions.append(len(text))

    if len(positions) <= 1:
        # ไม่เจอ boundary — ส่งเป็น block เดียว
        blocks.append({
            "id": 0, "type": "code", "level": 0,
            "heading_path": ext, "text": text[:8000],
            "page": None, "row_start": None,
        })
    else:
        for i in range(len(positions) - 1):
            chunk = text[positions[i]:positions[i + 1]].strip()
            if not chunk:
                continue
            # เอา signature บรรทัดแรกเป็น heading_path
            first_line = chunk.splitlines()[0].strip()
            blocks.append({
                "id": block_id,
                "type": "code",
                "level": 2,
                "heading_path": f"{ext} > {first_line[:60]}",
                "text": chunk,
                "page": None,
                "row_start": None,
            })
            block_id += 1

    return {
        "type": "structured",
        "format": "code",
        "title": "",
        "summary": f"Code ({ext})",
        "blocks": blocks,
        "content": text,
        "page_count": None,
    }


def process_text(data: bytes, ext: str = ".txt") -> DocResult:
    text = data.decode("utf-8-sig", errors="replace")

    # แบ่งตาม Markdown heading ถ้ามี ไม่งั้นแบ่งตาม paragraph
    md_headings = re.findall(r"^#{1,6}\s+.+", text, re.MULTILINE)
    blocks: List[Block] = []
    block_id = 0

    if md_headings:
        parts = re.split(r"(?=^#{1,6}\s+)", text, flags=re.MULTILINE)
        heading_stack: List[str] = []
        for part in parts:
            if not part.strip():
                continue
            lines = part.strip().splitlines()
            first = lines[0].strip()
            m = re.match(r"^(#{1,6})\s+(.+)", first)
            if m:
                level = len(m.group(1))
                heading = m.group(2).strip()
                while len(heading_stack) >= level:
                    if heading_stack:
                        heading_stack.pop()
                    else:
                        break
                heading_stack.append(heading)
                content_text = "\n".join(lines[1:]).strip()
                blocks.append({
                    "id": block_id, "type": "heading", "level": level,
                    "heading_path": " > ".join(heading_stack),
                    "text": heading, "page": None, "row_start": None,
                })
                block_id += 1
                if content_text:
                    blocks.append({
                        "id": block_id, "type": "paragraph", "level": 0,
                        "heading_path": " > ".join(heading_stack),
                        "text": content_text, "page": None, "row_start": None,
                    })
                    block_id += 1
            else:
                blocks.append({
                    "id": block_id, "type": "paragraph", "level": 0,
                    "heading_path": " > ".join(heading_stack),
                    "text": part.strip(), "page": None, "row_start": None,
                })
                block_id += 1
    else:
        # แบ่งตาม paragraph (double newline)
        paras = re.split(r"\n{2,}", text.strip())
        for para in paras:
            para = para.strip()
            if para:
                blocks.append({
                    "id": block_id, "type": "paragraph", "level": 0,
                    "heading_path": "",
                    "text": para, "page": None, "row_start": None,
                })
                block_id += 1

    return {
        "type": "structured",
        "format": ext.lstrip(".") or "text",
        "title": "",
        "summary": f"ไฟล์ข้อความ ({ext})",
        "blocks": blocks,
        "content": text,
        "page_count": None,
    }


def process_xml(data: bytes) -> DocResult:
    try:
        root = ET.fromstring(data.decode("utf-8", errors="replace"))
        def elem_to_text(el, depth=0) -> str:
            indent = "  " * depth
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            attrs = " ".join(f'{k}="{v}"' for k, v in el.attrib.items())
            text = (el.text or "").strip()
            parts = [f"{indent}<{tag}{' ' + attrs if attrs else ''}>"]
            if text:
                parts.append(f"{indent}  {text}")
            for child in el:
                parts.append(elem_to_text(child, depth + 1))
            return "\n".join(parts)
        content = elem_to_text(root)
    except Exception:
        content = data.decode("utf-8", errors="replace")

    blocks = [{
        "id": 0, "type": "paragraph", "level": 0,
        "heading_path": "", "text": content, "page": None, "row_start": None,
    }]
    return {
        "type": "structured", "format": "xml", "title": "",
        "summary": "XML", "blocks": blocks, "content": content, "page_count": None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Image
# ══════════════════════════════════════════════════════════════════════════════

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}
CODE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cpp", ".c",
             ".go", ".rs", ".rb", ".php", ".cs", ".swift", ".kt", ".sh",
             ".bash", ".sql", ".r", ".m", ".scala", ".lua"}


# ══════════════════════════════════════════════════════════════════════════════
# Router
# ══════════════════════════════════════════════════════════════════════════════

def route_file(filename: str, data: bytes) -> DocResult:
    """
    Entry point — รับไฟล์ คืน DocResult
    backward-compat: ยังมี result["content"] และ result["summary"] เสมอ
    """
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""

    if ext == ".pdf":
        return process_pdf(data)
    if ext in (".docx", ".doc"):
        return process_docx(data)
    if ext == ".csv":
        return process_csv(data)
    if ext in (".xlsx", ".xls"):
        return process_xlsx(data)
    if ext == ".xml":
        return process_xml(data)
    if ext in CODE_EXTS:
        return process_code(data, ext)
    if ext in (".txt", ".md", ".log", ".json", ".yaml", ".yml",
               ".html", ".htm"):
        return process_text(data, ext)
    if ext in IMAGE_EXTS:
        mime = MIME_MAP.get(ext, "image/jpeg")
        b64 = base64.b64encode(data).decode("utf-8")
        return {
            "type": "image",
            "format": "image",
            "title": "",
            "summary": "รูปภาพ",
            "blocks": [],
            "content": b64,
            "mime": mime,
            "page_count": None,
        }
    # fallback — treat as text
    return process_text(data, ext)