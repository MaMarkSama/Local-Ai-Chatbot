# -*- coding: utf-8 -*-
import os, io, sys, httpx, logging, pypdf, json, asyncio
from typing import List, Dict, Optional, AsyncGenerator
# [แก้ไขแล้ว] เพิ่ม BackgroundTasks + StreamingResponse
from fastapi import FastAPI, UploadFile, File, Request, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from conversation import ConversationManager
from database import db
from file_processor import route_file
from user_manager import user_manager
from memory_manager import MemoryManager
from web_searcher import web_searcher
from usage_tracker import usage_tracker
from smart_router import smart_router
from auth_middleware import get_current_user, require_user, require_admin
from message_turbovec import MessageTurboVec
from admin_tools import push_sqlite_snapshot
from security import SecurityHeadersMiddleware, cookie_options, rate_limit

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "gemma3:9b")
OLLAMA_VISION   = os.getenv("OLLAMA_VISION_MODEL", OLLAMA_MODEL)
SYSTEM_PROMPT   = os.getenv("SYSTEM_PROMPT", (
    "คุณคือผู้ช่วย AI ที่ฉลาดและเป็นมิตร ตอบคำถามภาษาไทยได้อย่างชัดเจน "
    "กระชับ และเป็นประโยชน์ หากไม่แน่ใจให้บอกตรง ๆ "
    "เมื่อเขียนโค้ดให้ใส่ภาษาของโค้ดหลัง ``` เสมอ เช่น ```python"
))
PDF_MAX_CHARS = int(os.getenv("PDF_MAX_CHARS", "20000"))
WEB_PORT      = int(os.getenv("WEB_PORT", "8001"))
TURBOVEC_ENABLED = os.getenv("TURBOVEC_ENABLED", "1").lower() not in ("0", "false", "no")
TURBOVEC_MIN_CHARS = int(os.getenv("TURBOVEC_MIN_CHARS", "8000"))  # Increased to 8000 (อ่านตรงๆ เร็วขึ้น)
TURBOVEC_CHUNK_CHARS = int(os.getenv("TURBOVEC_CHUNK_CHARS", "2000")) # Larger chunks = fewer requests to Ollama
TURBOVEC_TOP_K = int(os.getenv("TURBOVEC_TOP_K", "6"))
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

app = FastAPI(title="Gemma Web Chat")
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"],
                   allow_credentials=True)

conversation_manager = ConversationManager(max_turns=20)
memory_manager       = MemoryManager(OLLAMA_BASE_URL, OLLAMA_MODEL, SYSTEM_PROMPT)
message_turbovec     = MessageTurboVec(
    ollama_base_url=OLLAMA_BASE_URL,
    embed_model=OLLAMA_EMBED_MODEL,
    enabled=TURBOVEC_ENABLED,
)


# ── Models ────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    session_id: str
    message:    str
    model:      Optional[str] = None
    web_search: Optional[bool] = False
    library_search: Optional[bool] = False

class RenameRequest(BaseModel):
    title: str

class AuthRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""
    email: str = ""

class RoleRequest(BaseModel):
    role: str

class SyncRequest(BaseModel):
    limit: int = 5000


@app.middleware("http")
async def apply_rate_limit(request: Request, call_next):
    limited = await rate_limit(request)
    if limited:
        return limited
    return await call_next(request)


def _can_access_session(request: Request, session_id: str) -> bool:
    user = get_current_user(request)
    owner = db.get_session_owner(session_id)
    if not owner:
        return True
    if user and user.get("role") == "admin":
        return True
    if user and user["id"] == owner:
        return True
    if not user and owner == f"anon_{session_id}":
        return True
    return False


# ── Auth Endpoints ────────────────────────────────────────────────────────
@app.post("/api/auth/register")
async def register(req: AuthRequest):
    result = user_manager.register(
        req.username, req.password,
        email=req.email or None,
        display_name=req.display_name or req.username
    )
    if not result["ok"]:
        return JSONResponse(result, status_code=400)
    response = JSONResponse(result)
    response.set_cookie("gemma_token", result["token"], **cookie_options())
    return response

@app.post("/api/auth/login")
async def login(req: AuthRequest):
    result = user_manager.login(req.username, req.password)
    if not result["ok"]:
        return JSONResponse(result, status_code=401)
    response = JSONResponse(result)
    response.set_cookie("gemma_token", result["token"], **cookie_options())
    return response

@app.post("/api/auth/logout")
async def logout(request: Request):
    token = request.cookies.get("gemma_token")
    if token:
        user_manager.logout(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie("gemma_token")
    return response

@app.get("/api/auth/me")
async def me(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    return JSONResponse({"ok": True, "user": user})

@app.post("/api/auth/change-password")
async def change_password(request: Request, data: dict):
    user = require_user(request)
    result = user_manager.change_password(
        user["id"], data.get("old_password",""), data.get("new_password",""))
    return JSONResponse(result)


# ── Ollama Chat ───────────────────────────────────────────────────────────

def _build_payload(user_id: str, session_id: str, user_message: str,
                   model: str = None, stream: bool = False) -> dict:
    """สร้าง Ollama payload โดยใส่ memory context + conversation history"""
    enhanced_prompt = memory_manager.get_enhanced_system_prompt(user_id, session_id)
    messages = conversation_manager.get_messages(session_id)
    payload_messages = (
        [{"role": "system", "content": enhanced_prompt}]
        + messages[:-1]
        + [{"role": "user", "content": user_message}]
    )
    return {
        "model":    model or OLLAMA_MODEL,
        "messages": payload_messages,
        "stream":   stream,
        "options":  {"temperature": 0.7, "num_predict": -1, "repeat_penalty": 1.1, "num_ctx": OLLAMA_NUM_CTX},
    }


async def chat_with_ollama(user_id: str, session_id: str,
                            user_message: str, source: str = "webchat",
                            background_tasks: BackgroundTasks = None,
                            model: str = None, display_message: str = None) -> str:
    """Non-streaming version — ใช้สำหรับ upload-file และ internal calls"""
    messages = conversation_manager.get_messages(session_id)
    messages = await memory_manager.maybe_summarize(user_id, session_id, messages)

    msg_to_save = display_message if display_message is not None else user_message
    conversation_manager.add_message(session_id, "user", msg_to_save, source, user_id=user_id)

    payload = _build_payload(user_id, session_id, user_message, model=model, stream=False)

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
            data    = r.json()
            reply   = data["message"]["content"].strip()
            p_tokens = data.get("prompt_eval_count", 0)
            c_tokens = data.get("eval_count", 0)

        conversation_manager.add_message(session_id, "assistant", reply, source, user_id=user_id)
        usage_tracker.record_usage(session_id, p_tokens, c_tokens)

        # background task — ใช้ BackgroundTasks ถ้ามี ไม่งั้น fire-and-forget ที่ปลอดภัย
        if background_tasks:
            background_tasks.add_task(memory_manager.extract_key_info, user_id, user_message, reply)
        else:
            asyncio.ensure_future(memory_manager.extract_key_info(user_id, user_message, reply))

        return reply or "ขอโทษครับ ไม่สามารถสร้างคำตอบได้"
    except httpx.ConnectError:
        return "⚠️ ไม่สามารถเชื่อมต่อกับ Ollama ได้"
    except httpx.TimeoutException:
        return "⏳ Ollama ใช้เวลานานเกินไป กรุณาลองใหม่"
    except Exception as e:
        return f"❌ เกิดข้อผิดพลาด: {e}"


async def _stream_ollama(user_id: str, session_id: str,
                          user_message: str, display_message: str,
                          source: str, model: str,
                          background_tasks: BackgroundTasks) -> AsyncGenerator[str, None]:
    """
    Async generator สำหรับ SSE streaming
    ส่งข้อมูลเป็น Server-Sent Events format:
      data: {"token": "..."}\n\n   ← ระหว่างสร้างคำตอบ
      data: {"done": true, "full": "..."}\n\n  ← เมื่อเสร็จ
      data: {"error": "..."}\n\n   ← เมื่อเกิดข้อผิดพลาด
    """
    # เตรียม messages + memory ก่อน stream
    messages = conversation_manager.get_messages(session_id)
    messages = await memory_manager.maybe_summarize(user_id, session_id, messages)

    msg_to_save = display_message if display_message is not None else user_message
    conversation_manager.add_message(session_id, "user", msg_to_save, source, user_id=user_id)

    payload = _build_payload(user_id, session_id, user_message, model=model, stream=True)

    full_reply   = []
    p_tokens     = 0
    c_tokens     = 0

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", f"{OLLAMA_BASE_URL}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full_reply.append(token)
                        yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"

                    # Ollama ส่ง done=true พร้อม token stats ใน chunk สุดท้าย
                    if chunk.get("done"):
                        p_tokens = chunk.get("prompt_eval_count", 0)
                        c_tokens = chunk.get("eval_count", 0)

        reply = "".join(full_reply).strip() or "ขอโทษครับ ไม่สามารถสร้างคำตอบได้"
        conversation_manager.add_message(session_id, "assistant", reply, source, user_id=user_id)
        usage_tracker.record_usage(session_id, p_tokens, c_tokens)

        if background_tasks:
            background_tasks.add_task(memory_manager.extract_key_info, user_id, user_message, reply)
        else:
            asyncio.ensure_future(memory_manager.extract_key_info(user_id, user_message, reply))

        yield f"data: {json.dumps({'done': True, 'full': reply}, ensure_ascii=False)}\n\n"

    except httpx.ConnectError:
        yield f"data: {json.dumps({'error': '⚠️ ไม่สามารถเชื่อมต่อกับ Ollama ได้'})}\n\n"
    except httpx.TimeoutException:
        yield f"data: {json.dumps({'error': '⏳ Ollama ใช้เวลานานเกินไป กรุณาลองใหม่'})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': f'❌ เกิดข้อผิดพลาด: {e}'})}\n\n"


async def analyze_image(user_id: str, session_id: str,
                         b64: str, mime: str, prompt: str,
                         source: str = "webchat", model: str = None) -> str:
    user_prompt = prompt or "กรุณาวิเคราะห์และอธิบายรูปภาพนี้"
    payload = {
        "model":    model or OLLAMA_VISION,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt, "images": [b64]},
        ],
        "stream":  False,
        "options": {"temperature": 0.7, "num_predict": -1, "num_ctx": OLLAMA_NUM_CTX},
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
            reply = r.json()["message"]["content"].strip()
        conversation_manager.add_message(session_id, "user", f"[รูปภาพ] {user_prompt}", source)
        conversation_manager.add_message(session_id, "assistant", reply, source, user_id=user_id)
        return reply or "ไม่สามารถวิเคราะห์รูปภาพได้"
    except Exception as e:
        return f"❌ วิเคราะห์รูปภาพไม่ได้: {e}"


async def _smart_summarize_legacy(user_id: str, session_id: str,
                           filename: str, content: str,
                           file_type: str, source: str = "webchat",
                           background_tasks: BackgroundTasks = None,
                           model: str = None) -> str:
    CHUNK = 6000
    if len(content) <= CHUNK:
        prompt = (
            f"ไฟล์ชื่อ '{filename}' ({file_type}):\n\n{content}\n\n"
            "สรุปใจความสำคัญ ประเด็นหลัก และข้อมูลสำคัญให้กระชับครบถ้วน"
        )
        return await chat_with_ollama(user_id, session_id, prompt, source, background_tasks=background_tasks, model=model)

    chunks = [content[i:i+CHUNK] for i in range(0, len(content), CHUNK)]
    summaries = []
    for i, chunk in enumerate(chunks, 1):
        prompt = f"ส่วนที่ {i}/{len(chunks)} ของ '{filename}':\n\n{chunk}\n\nสรุปประเด็นสำคัญ"
        try:
            flags = {"creationflags": 0x08000000} if sys.platform=="win32" else {}
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json={
                    "model":    model or OLLAMA_MODEL,
                    "messages": [{"role":"system","content":SYSTEM_PROMPT},
                                 {"role":"user","content":prompt}],
                    "stream":   False,
                    "options":  {"temperature":0.5,"num_predict":512, "num_ctx": OLLAMA_NUM_CTX},
                })
                summaries.append(r.json()["message"]["content"].strip())
        except Exception as e:
            summaries.append(f"(ส่วนที่ {i} ล้มเหลว: {e})")

    combined = "\n\n".join(f"ส่วนที่ {i+1}: {s}" for i,s in enumerate(summaries))
    final    = f"สรุปภาพรวมของ '{filename}' ({len(chunks)} ส่วน):\n\n{combined}\n\nสรุปรวมทั้งหมดให้กระชับ"
    return await chat_with_ollama(user_id, session_id, final, source, background_tasks=background_tasks, model=model)


async def smart_summarize(user_id: str, session_id: Optional[str],
                           filename: str, content: str,
                           file_type: str, user_prompt: str = "",
                           source: str = "webchat",
                           background_tasks: BackgroundTasks = None,
                           model: str = None,
                           doc_result: dict = None) -> str:

    display_msg = f"[📄 แนบไฟล์: {filename}] {user_prompt}" if user_prompt else f"[📄 แนบไฟล์: {filename}] ขอสรุปเนื้อหาในเอกสารนี้"
    query = (user_prompt or "").strip() or f"สรุปใจความสำคัญ ประเด็นหลัก ข้อมูลสำคัญของไฟล์ {filename}"

    if len(content) <= TURBOVEC_MIN_CHARS:
        prompt = (
            f"คุณเป็นผู้เชี่ยวชาญด้านการวิเคราะห์เอกสาร\n"
            f"กรุณาวิเคราะห์ไฟล์ชื่อ '{filename}' ({file_type}) จากเนื้อหาด้านล่างนี้:\n\n"
            f"[เนื้อหาเอกสาร]\n{content}\n\n"
            f"[คำสั่งการวิเคราะห์]\n"
            f"ตอบคำถามหรือทำตามคำสั่งของผู้ใช้ หากผู้ใช้ไม่ระบุ ให้สรุปประเด็นสำคัญที่สุด (Executive Summary) "
            f"และสกัดข้อมูลสำคัญออกมาเป็น Bullet points\n"
            f"คำขอของผู้ใช้: {query}"
        )
        return await chat_with_ollama(user_id, session_id, prompt, source, background_tasks=background_tasks, model=model, display_message=display_msg)

    try:
        index_info = await message_turbovec.index_document(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            content=content,
            chunk_chars=TURBOVEC_CHUNK_CHARS,
            doc_result=doc_result,
        )
        matches = await message_turbovec.search(
            user_id=user_id,
            session_id=session_id,
            query=query,
            file_hash=index_info["file_hash"],
            top_k=TURBOVEC_TOP_K,
        )
    except Exception as e:
        logger.exception("MessageTurboVec failed")
        fallback = content[:TURBOVEC_MIN_CHARS]
        prompt = (
            f"ไฟล์ '{filename}' ({file_type}) ยาวมาก แต่สร้าง vector index ไม่สำเร็จ: {e}\n\n"
            f"เนื้อหาช่วงต้น:\n{fallback}\n\n"
            f"คำขอ: {query}\nตอบจากข้อมูลที่มีให้กระชับ และบอกข้อจำกัดถ้าข้อมูลไม่พอ"
        )
        return await chat_with_ollama(user_id, session_id, prompt, source, background_tasks=background_tasks, model=model, display_message=display_msg)

    if not matches:
        prompt = (
            f"ไฟล์ '{filename}' ({file_type}) ถูก index แล้ว แต่ไม่พบส่วนที่เกี่ยวข้องกับคำขอ: {query}\n"
            "ให้ตอบว่าต้องการคำถามที่เฉพาะเจาะจงขึ้น"
        )
        return await chat_with_ollama(user_id, session_id, prompt, source, background_tasks=background_tasks, model=model, display_message=display_msg)

    context = "\n\n".join(
        (f"[หัวข้อ: {m['heading_path']}]\n" if m.get('heading_path') else "")
        + f"[ส่วนที่ {m['chunk_index'] + 1} | {m.get('chunk_type','semantic')} | score={m['score']:.2f}]\n{m['content']}"
        for m in matches
    )
    prompt = f"""คุณเป็นผู้เชี่ยวชาญด้านการวิเคราะห์เอกสาร
ผู้ใช้อัปโหลดไฟล์ '{filename}' ({file_type}) ระบบได้ดึงเฉพาะส่วนที่เกี่ยวข้องที่สุดมาให้คุณวิเคราะห์:

[บริบทจากเอกสาร]
{context}

[คำขอของผู้ใช้]
{query}

[คำสั่งการวิเคราะห์]
ตอบโดยอ้างอิงเฉพาะบริบทจากเอกสารด้านบน หากข้อมูลไม่พอให้บอกตรงๆ สรุปข้อมูลให้กระชับ อ่านง่าย และใช้ Bullet points ตามความเหมาะสม"""
    
    return await chat_with_ollama(user_id, session_id, prompt, source, background_tasks=background_tasks, model=model, display_message=display_msg)


@app.post("/chat")
async def chat(req: ChatRequest, request: Request, background_tasks: BackgroundTasks):
    # IDOR check
    if not _can_access_session(request, req.session_id):
        return JSONResponse({"error": "Unauthorized Access to Session"}, status_code=403)

    user    = get_current_user(request)
    user_id = user["id"] if user else f"anon_{req.session_id}"

    if req.message.strip().lower() in ["/reset", "ลืมทุกอย่าง", "เริ่มใหม่"]:
        conversation_manager.clear(req.session_id)
        db.clear_memories(user_id, req.session_id)
        # ส่งเป็น SSE เพื่อให้ frontend รับได้ทั้งสองแบบ
        async def _reset_stream():
            msg = "🔄 ล้างประวัติและ memory แล้ว เริ่มต้นใหม่ได้เลยครับ!"
            yield f"data: {json.dumps({'token': msg})}\n\n"
            yield f"data: {json.dumps({'done': True, 'full': msg})}\n\n"
        return StreamingResponse(_reset_stream(), media_type="text/event-stream")

    user_message = req.message

    # ── Smart Routing ──────────────────────────────────────────────────────
    need_web = req.web_search
    need_lib = getattr(req, "library_search", False)

    if not need_web and not need_lib:
        need_web, need_lib = await smart_router.decide_tools(user_message)

    # ── Context injection (web + library) ─────────────────────────────────
    if need_web:
        search_results = await web_searcher.search(user_message)   # ← async แล้ว
        if search_results:
            user_message = (
                f"[ข้อมูลล่าสุดจากอินเทอร์เน็ต]\n{search_results}\n\n"
                f"[คำแนะนำ]\nกรุณาตอบโดยใช้ข้อมูลจาก Web Search เป็นหลัก "
                f"หากข้อมูลระบุวันที่หรือตัวเลขให้ใช้ที่เป็นปัจจุบันที่สุด\n\n"
                f"[คำถามของผู้ใช้]\n{user_message}"
            )
        else:
            user_message = (
                f"(ระบบพยายามค้นหาข้อมูลล่าสุดจากอินเทอร์เน็ตแล้วแต่ไม่พบผลลัพธ์ที่เกี่ยวข้อง)\n"
                f"คำถามของผู้ใช้: {user_message}"
            )

    if need_lib and user:
        matches = await message_turbovec.search(user_id, None, req.message, top_k=6)
        if matches:
            lib_context = "\n\n".join(
                f"[ข้อมูลจากไฟล์: {m['filename']}]\n{m['content']}" for m in matches
            )
            user_message = (
                f"[ข้อมูลจากคลังไฟล์ส่วนตัวของผู้ใช้]\n{lib_context}\n\n"
                f"[คำแนะนำ]\nใช้ข้อมูลจากไฟล์ด้านบนเพื่อตอบคำถาม "
                f"หากข้อมูลมาจากหลายไฟล์ให้ระบุชื่อไฟล์ที่อ้างอิงด้วย\n\n"
                f"[คำถามของผู้ใช้]\n{user_message}"
            )

    # ── Stream response ────────────────────────────────────────────────────
    return StreamingResponse(
        _stream_ollama(
            user_id, req.session_id,
            user_message, req.message,
            "webchat", req.model,
            background_tasks,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # ปิด nginx buffering ถ้ามี reverse proxy
        },
    )


@app.post("/upload-file")
async def upload_file(request: Request, session_id: str,
                       user_prompt: str = "", model: str = None, file: UploadFile = File(...),
                       background_tasks: BackgroundTasks = None):
    if not _can_access_session(request, session_id):
        return JSONResponse({"error": "Unauthorized Access to Session"}, status_code=403)

    user    = get_current_user(request)
    user_id = user["id"] if user else f"anon_{session_id}"
    data    = await file.read()
    result  = route_file(file.filename, data)
    filename = file.filename
    file_hash = message_turbovec.file_hash(filename, result["content"])

    # ── Image: ไม่มี streaming path ใน vision model — ส่ง SSE เพื่อ UX สม่ำเสมอ ──
    if result["type"] == "image":
        async def _image_stream():
            yield f"data: {json.dumps({'status': 'กำลังวิเคราะห์รูปภาพด้วย AI...'}, ensure_ascii=False)}\n\n"
            reply = await analyze_image(user_id, session_id,
                                        result["content"], result["mime"],
                                        user_prompt or "วิเคราะห์รูปภาพนี้", model=model)
            yield f"data: {json.dumps({'done': True, 'full': reply, 'filename': filename, 'file_type': result['summary']}, ensure_ascii=False)}\n\n"

        return StreamingResponse(_image_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── Document: streaming pipeline ──────────────────────────────────────
    async def _doc_stream():
        content  = result["content"]
        summary  = result["summary"]
        query    = (user_prompt or "").strip() or f"สรุปใจความสำคัญ ประเด็นหลัก ข้อมูลสำคัญของไฟล์ {filename}"
        display_msg = f"[📄 แนบไฟล์: {filename}] {user_prompt}" if user_prompt else f"[📄 แนบไฟล์: {filename}] ขอสรุปเนื้อหาในเอกสารนี้"

        # ── Phase 1: อ่านและเตรียมไฟล์ ────────────────────────────────────
        yield f"data: {json.dumps({'status': f'📄 กำลังอ่านไฟล์ {filename}...'}, ensure_ascii=False)}\n\n"

        # ── Phase 2: ตัดสินใจ path (short / RAG) ──────────────────────────
        if len(content) <= TURBOVEC_MIN_CHARS:
            # เอกสารสั้น — ส่งตรงๆ เข้า Ollama พร้อม stream
            yield f"data: {json.dumps({'status': '🧠 กำลังวิเคราะห์เนื้อหา...'}, ensure_ascii=False)}\n\n"
            prompt = (
                f"คุณเป็นผู้เชี่ยวชาญด้านการวิเคราะห์เอกสาร\n"
                f"กรุณาวิเคราะห์ไฟล์ชื่อ '{filename}' ({summary}) จากเนื้อหาด้านล่างนี้:\n\n"
                f"[เนื้อหาเอกสาร]\n{content}\n\n"
                f"[คำสั่งการวิเคราะห์]\n"
                f"ตอบคำถามหรือทำตามคำสั่งของผู้ใช้ หากผู้ใช้ไม่ระบุ ให้สรุปประเด็นสำคัญที่สุด (Executive Summary) "
                f"และสกัดข้อมูลสำคัญออกมาเป็น Bullet points\n"
                f"คำขอของผู้ใช้: {query}"
            )
        else:
            # เอกสารยาว — ทำ RAG แล้ว stream ผล
            block_count = len(result.get("blocks", []))
            status_msg = (
                f"🔍 กำลังสร้าง Vector Index ({block_count} blocks)..."
                if block_count else
                f"🔍 กำลังสร้าง Vector Index ({len(content):,} ตัวอักษร)..."
            )
            yield f"data: {json.dumps({'status': status_msg}, ensure_ascii=False)}\n\n"
            try:
                index_info = await message_turbovec.index_document(
                    user_id=user_id, session_id=session_id,
                    filename=filename, content=content,
                    chunk_chars=TURBOVEC_CHUNK_CHARS,
                    doc_result=result,
                )
                reused = index_info.get("reused", False)
                reused_label = " (ใช้ Index เดิม)" if reused else ""
                yield f"data: {json.dumps({'status': f'🔎 ค้นหาส่วนที่เกี่ยวข้อง{reused_label}...'}, ensure_ascii=False)}\n\n"
                matches = await message_turbovec.search(
                    user_id=user_id, session_id=session_id, query=query,
                    file_hash=index_info["file_hash"], top_k=TURBOVEC_TOP_K,
                )
            except Exception as e:
                logger.exception("MessageTurboVec failed in stream")
                matches = []
                fallback = content[:TURBOVEC_MIN_CHARS]
                prompt = (
                    f"ไฟล์ '{filename}' ({summary}) ยาวมาก แต่สร้าง vector index ไม่สำเร็จ: {e}\n\n"
                    f"เนื้อหาช่วงต้น:\n{fallback}\n\n"
                    f"คำขอ: {query}\nตอบจากข้อมูลที่มีให้กระชับ และบอกข้อจำกัดถ้าข้อมูลไม่พอ"
                )
                yield f"data: {json.dumps({'status': '⚠️ Vector Index ไม่สำเร็จ ใช้เนื้อหาช่วงต้นแทน...'}, ensure_ascii=False)}\n\n"
                # fall through ด้วย prompt ที่ set ไว้แล้ว
            else:
                if not matches:
                    prompt = (
                        f"ไฟล์ '{filename}' ({summary}) ถูก index แล้ว แต่ไม่พบส่วนที่เกี่ยวข้องกับคำขอ: {query}\n"
                        "ให้ตอบว่าต้องการคำถามที่เฉพาะเจาะจงขึ้น"
                    )
                else:
                    context = "\n\n".join(
                        f"[ส่วนที่ {m['chunk_index'] + 1} | score={m['score']:.2f}]\n{m['content']}"
                        for m in matches
                    )
                    prompt = (
                        f"คุณเป็นผู้เชี่ยวชาญด้านการวิเคราะห์เอกสาร\n"
                        f"ผู้ใช้อัปโหลดไฟล์ '{filename}' ({summary}) "
                        f"ระบบดึงเฉพาะ {len(matches)} ส่วนที่เกี่ยวข้องที่สุดมาให้วิเคราะห์:\n\n"
                        f"[บริบทจากเอกสาร]\n{context}\n\n"
                        f"[คำขอของผู้ใช้]\n{query}\n\n"
                        f"[คำสั่งการวิเคราะห์]\n"
                        f"ตอบโดยอ้างอิงเฉพาะบริบทจากเอกสารด้านบน หากข้อมูลไม่พอให้บอกตรงๆ "
                        f"สรุปข้อมูลให้กระชับ อ่านง่าย และใช้ Bullet points ตามความเหมาะสม"
                    )
                yield f"data: {json.dumps({'status': '🧠 กำลังวิเคราะห์และสร้างคำตอบ...'}, ensure_ascii=False)}\n\n"

        # ── Phase 3: Stream Ollama response ───────────────────────────────
        messages = conversation_manager.get_messages(session_id)
        messages = await memory_manager.maybe_summarize(user_id, session_id, messages)
        conversation_manager.add_message(session_id, "user", display_msg, "webchat", user_id=user_id)

        enhanced_prompt = memory_manager.get_enhanced_system_prompt(user_id, session_id)
        history = conversation_manager.get_messages(session_id)
        payload = {
            "model": model or OLLAMA_MODEL,
            "messages": (
                [{"role": "system", "content": enhanced_prompt}]
                + history[:-1]
                + [{"role": "user", "content": prompt}]
            ),
            "stream": True,
            "options": {"temperature": 0.7, "num_predict": -1, "repeat_penalty": 1.1, "num_ctx": OLLAMA_NUM_CTX},
        }

        full_reply = []
        p_tokens = c_tokens = 0
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream("POST", f"{OLLAMA_BASE_URL}/api/chat", json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            full_reply.append(token)
                            yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        if chunk.get("done"):
                            p_tokens = chunk.get("prompt_eval_count", 0)
                            c_tokens = chunk.get("eval_count", 0)

            reply = "".join(full_reply).strip() or "ขอโทษครับ ไม่สามารถสร้างคำตอบได้"
            conversation_manager.add_message(session_id, "assistant", reply, "webchat", user_id=user_id)
            usage_tracker.record_usage(session_id, p_tokens, c_tokens)

            # บันทึก file meta หลังตอบสำเร็จ
            if user:
                db.save_file_meta(user_id, filename, file_hash, summary, reply[:200])

            if background_tasks:
                background_tasks.add_task(memory_manager.extract_key_info, user_id, display_msg, reply)
            else:
                asyncio.ensure_future(memory_manager.extract_key_info(user_id, display_msg, reply))

            yield f"data: {json.dumps({'done': True, 'full': reply, 'filename': filename, 'file_type': summary}, ensure_ascii=False)}\n\n"

        except httpx.ConnectError:
            yield f"data: {json.dumps({'error': '⚠️ ไม่สามารถเชื่อมต่อกับ Ollama ได้'})}\n\n"
        except httpx.TimeoutException:
            yield f"data: {json.dumps({'error': '⏳ Ollama ใช้เวลานานเกินไป กรุณาลองใหม่'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'❌ เกิดข้อผิดพลาด: {e}'})}\n\n"

    return StreamingResponse(_doc_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Session API ───────────────────────────────────────────────────────────

@app.get("/api/models")
async def get_available_models():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if r.status_code == 200:
                models = []
                for m in r.json().get("models", []):
                    models.append({
                        "name": m["name"],
                        "size": m.get("size", 0),
                        "parameter_size": m.get("details", {}).get("parameter_size", "Unknown")
                    })
                return {"models": models, "current": OLLAMA_MODEL}
    except Exception:
        pass
    return {"models": [{"name": OLLAMA_MODEL, "size": 0, "parameter_size": "Unknown"}], "current": OLLAMA_MODEL}

@app.get("/api/sessions")
async def get_sessions(request: Request, source: str = None, session_id: str = None):
    user = get_current_user(request)
    if user:
        sessions = db.get_user_sessions(user["id"], source=source or "webchat")
    else:
        # สำหรับ Guest ให้เห็นเฉพาะ session ปัจจุบันของตัวเองเท่านั้น (Privacy Fix)
        all_anon = db.get_sessions(source=source or "webchat", user_id=None)
        if session_id:
            sessions = [s for s in all_anon if s["id"] == session_id]
        else:
            # ถ้าไม่ระบุ session ให้คืนค่าว่างสำหรับ Guest (ต้องเข้าผ่าน ID ตรงๆ)
            sessions = []
    return JSONResponse(sessions)

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    # [แก้ไขแล้ว] เพิ่ม IDOR เช็คสิทธิ์
    if not _can_access_session(request, session_id):
        return JSONResponse({"error": "Unauthorized Access to Session"}, status_code=403)

    msgs = db.get_session_messages(session_id)
    s    = next((x for x in db.get_sessions() if x["id"] == session_id), {})
    return JSONResponse({"session_id": session_id,
                         "title": s.get("title"), "messages": msgs})

@app.post("/api/sessions/{session_id}/rename")
async def rename_session(session_id: str, req: RenameRequest, request: Request):
    # [แก้ไขแล้ว] เพิ่ม IDOR เช็คสิทธิ์
    if not _can_access_session(request, session_id):
        return JSONResponse({"error": "Unauthorized Access to Session"}, status_code=403)

    db.rename_session(session_id, req.title)
    return JSONResponse({"status": "ok"})

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    # [แก้ไขแล้ว] เพิ่ม IDOR เช็คสิทธิ์
    if not _can_access_session(request, session_id):
        return JSONResponse({"error": "Unauthorized Access to Session"}, status_code=403)

    user = get_current_user(request)
    user_id = user["id"] if user else None
    db.delete_session(session_id)
    conversation_manager.clear(session_id)
    if user_id:
        db.clear_memories(user_id, session_id)
    return JSONResponse({"status": "ok"})

@app.get("/api/search")
async def search(q: str, request: Request):
    user = get_current_user(request)
    return JSONResponse(db.search_messages(q, limit=30))

@app.get("/api/memory")
async def get_memory(request: Request, session_id: str = None):
    user = require_user(request)
    memories = db.get_memories(user["id"], session_id)
    return JSONResponse(memories)

@app.delete("/api/memory")
async def clear_memory(request: Request):
    user = require_user(request)
    db.clear_memories(user["id"])
    return JSONResponse({"status": "ok"})


@app.get("/api/library")
async def get_library(request: Request):
    user = require_user(request)
    return JSONResponse(db.get_user_files(user["id"]))

@app.delete("/api/library/{file_hash}")
async def delete_library_file(file_hash: str, request: Request):
    user = require_user(request)
    db.delete_file(user["id"], file_hash)
    return JSONResponse({"status": "ok"})


@app.get("/api/usage/me")
async def get_my_usage(request: Request):
    user = require_user(request)
    return JSONResponse(usage_tracker.get_user_stats(user["id"]))

@app.get("/api/admin/usage")
async def get_admin_usage(request: Request):
    require_admin(request)
    return JSONResponse(usage_tracker.get_all_usage())

@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r      = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            return {"status":"ok","ollama":"online","model":OLLAMA_MODEL,
                    "model_loaded":any(OLLAMA_MODEL in m for m in models)}
    except Exception:
        return {"status":"ok","ollama":"offline"}

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=open(
        os.path.join(os.path.dirname(__file__), "webchat_ui.html"),
        encoding="utf-8"
    ).read() if os.path.exists(
        os.path.join(os.path.dirname(__file__), "webchat_ui.html")
    ) else HTML)


# ── Embedded HTML fallback ─────────────────────────────────────────────────
HTML = ""


if __name__ == "__main__":
    import uvicorn, socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]; s.close()
    except Exception:
        local_ip = "localhost"
    print(f"Gemma Web Chat v7")
    print(f"  Chat:    http://localhost:{WEB_PORT}")
    print(f"  Network: http://{local_ip}:{WEB_PORT}")
    uvicorn.run("webchat:app", host="0.0.0.0", port=WEB_PORT, reload=False)