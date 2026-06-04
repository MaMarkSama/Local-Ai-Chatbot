"""
file_processor.py — อ่านและแปลงไฟล์หลายประเภทเป็นข้อความ
รองรับ: PDF, CSV, XML, DOCX, XLSX, TXT, รูปภาพ
"""
import io
import base64
import csv
import xml.etree.ElementTree as ET
from typing import Tuple

MAX_CHARS = 12000


def process_pdf(data: bytes, max_chars: int = MAX_CHARS) -> Tuple[str, int]:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(data))
    total_pages = len(reader.pages)
    pages_text  = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if text:
            pages_text.append((i + 1, text))
    if not pages_text:
        return "(ไม่พบข้อความในไฟล์ PDF — อาจเป็น PDF รูปภาพ)", total_pages
    chunks, total = [], 0
    for page_num, text in pages_text:
        chunk = f"[หน้า {page_num}/{total_pages}]\n{text}"
        chunks.append(chunk)
        total += len(text)
        if total >= max_chars:
            chunks.append(f"\n...(แสดง {page_num}/{total_pages} หน้า)")
            break
    return "\n\n".join(chunks), total_pages


def process_csv(data: bytes, max_rows: int = 200) -> str:
    try:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("tis-620")
            except UnicodeDecodeError:
                text = data.decode("windows-874", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows   = list(reader)
        headers = reader.fieldnames or []
        total   = len(rows)
        lines   = [
            f"CSV — {total} แถว, {len(headers)} คอลัมน์",
            f"คอลัมน์: {', '.join(headers)}", "",
        ]
        for i, row in enumerate(rows[:max_rows], 1):
            lines.append(f"แถว {i}: " + " | ".join(f"{k}={v}" for k, v in row.items()))
        if total > max_rows:
            lines.append(f"...(แสดง {max_rows}/{total} แถว)")
        return "\n".join(lines)
    except Exception as e:
        return f"(อ่าน CSV ไม่ได้: {e})"


def process_xml(data: bytes, max_chars: int = MAX_CHARS) -> str:
    try:
        root = ET.fromstring(data.decode("utf-8", errors="replace"))
        def elem_to_text(el, depth=0) -> str:
            indent = "  " * depth
            tag    = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            attrs  = " ".join(f'{k}="{v}"' for k, v in el.attrib.items())
            text   = (el.text or "").strip()
            parts  = [f"{indent}<{tag}{' '+attrs if attrs else ''}>"]
            if text:
                parts.append(f"{indent}  {text}")
            for child in el:
                parts.append(elem_to_text(child, depth + 1))
            return "\n".join(parts)
        result = elem_to_text(root)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...(ถูกตัด)"
        return result
    except Exception as e:
        try:
            raw = data.decode("utf-8", errors="replace")
            return raw[:max_chars]
        except Exception:
            return f"(อ่าน XML ไม่ได้: {e})"


def process_docx(data: bytes, max_chars: int = MAX_CHARS) -> str:
    try:
        from docx import Document
        doc   = Document(io.BytesIO(data))
        lines = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            if para.style.name.startswith("Heading"):
                lines.append(f"\n## {text}")
            else:
                lines.append(text)
        for table in doc.tables:
            lines.append("\n[ตาราง]")
            for row in table.rows:
                lines.append(" | ".join(c.text.strip() for c in row.cells))
        result = "\n".join(lines)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...(ถูกตัด)"
        return result or "(ไม่พบข้อความใน DOCX)"
    except Exception as e:
        return f"(อ่าน DOCX ไม่ได้: {e})"


def process_xlsx(data: bytes, max_rows: int = 200) -> str:
    try:
        import openpyxl
        wb    = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for sheet_name in wb.sheetnames:
            ws   = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            parts.append(f"\n[Sheet: {sheet_name}] — {len(rows)} แถว")
            for i, row in enumerate(rows[:max_rows], 1):
                parts.append(f"แถว {i}: " + " | ".join(str(c) if c is not None else "" for c in row))
            if len(rows) > max_rows:
                parts.append(f"...(แสดง {max_rows}/{len(rows)} แถว)")
        return "\n".join(parts) or "(ไม่พบข้อมูลใน XLSX)"
    except Exception as e:
        return f"(อ่าน XLSX ไม่ได้: {e})"


def process_text(data: bytes, max_chars: int = MAX_CHARS) -> str:
    try:
        text = data.decode("utf-8-sig", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...(ถูกตัด)"
        return text
    except Exception as e:
        return f"(อ่านไฟล์ไม่ได้: {e})"


IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
MIME_MAP    = {
    ".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".png":"image/png",
    ".gif":"image/gif",  ".webp":"image/webp", ".bmp":"image/bmp",
}


def route_file(filename: str, data: bytes) -> dict:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""

    if ext == ".pdf":
        text, pages = process_pdf(data)
        return {"type": "text", "content": text, "summary": f"PDF {pages} หน้า"}

    if ext == ".csv":
        return {"type": "text", "content": process_csv(data), "summary": "CSV"}

    if ext == ".xml":
        return {"type": "text", "content": process_xml(data), "summary": "XML"}

    if ext in (".docx", ".doc"):
        return {"type": "text", "content": process_docx(data), "summary": "Word Document"}

    if ext in (".xlsx", ".xls"):
        return {"type": "text", "content": process_xlsx(data), "summary": "Excel Spreadsheet"}

    if ext in (".txt", ".md", ".log", ".json", ".yaml", ".yml", ".html", ".htm", ".py", ".js"):
        return {"type": "text", "content": process_text(data), "summary": f"ไฟล์ข้อความ ({ext})"}

    if ext in IMAGE_TYPES:
        mime = MIME_MAP.get(ext, "image/jpeg")
        b64  = base64.b64encode(data).decode("utf-8")
        return {"type": "image", "content": b64, "mime": mime, "summary": "รูปภาพ"}

    return {"type": "text", "content": process_text(data), "summary": "ไฟล์"}
