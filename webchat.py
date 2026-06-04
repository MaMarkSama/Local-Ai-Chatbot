# -*- coding: utf-8 -*-
import os, io, sys, httpx, logging, pypdf
from typing import List, Dict, Optional
# [แก้ไขแล้ว] เพิ่ม BackgroundTasks
from fastapi import FastAPI, UploadFile, File, Request, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from conversation import ConversationManager
from database import db
from file_processor import route_file
from user_manager import user_manager
from memory_manager import MemoryManager
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
TURBOVEC_MIN_CHARS = int(os.getenv("TURBOVEC_MIN_CHARS", "5000"))
TURBOVEC_CHUNK_CHARS = int(os.getenv("TURBOVEC_CHUNK_CHARS", "1200"))
TURBOVEC_TOP_K = int(os.getenv("TURBOVEC_TOP_K", "6"))
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

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
# [แก้ไขแล้ว] เพิ่มรับพารามิเตอร์ background_tasks
async def chat_with_ollama(user_id: str, session_id: str,
                            user_message: str, source: str = "webchat",
                            background_tasks: BackgroundTasks = None, 
                            model: str = None) -> str:
    # สรุป memory ถ้าบทสนทนายาวเกิน
    messages = conversation_manager.get_messages(session_id)
    messages = await memory_manager.maybe_summarize(user_id, session_id, messages)

    # เพิ่มข้อความใหม่
    conversation_manager.add_message(session_id, "user", user_message, source, user_id=user_id)
    messages = conversation_manager.get_messages(session_id)

    # System prompt + memory context
    enhanced_prompt = memory_manager.get_enhanced_system_prompt(user_id, session_id)

    payload = {
        "model":    model or OLLAMA_MODEL,
        "messages": [{"role": "system", "content": enhanced_prompt}] + messages,
        "stream":   False,
        "options":  {"temperature": 0.7, "num_predict": -1, "repeat_penalty": 1.1},
    }

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
            reply = r.json()["message"]["content"].strip()

        conversation_manager.add_message(session_id, "assistant", reply, source, user_id=user_id)

        # [แก้ไขแล้ว] ใช้ BackgroundTasks แทนเพื่อความเสถียร
        if background_tasks:
            background_tasks.add_task(memory_manager.extract_key_info, user_id, user_message, reply)
        else:
            import asyncio
            asyncio.create_task(memory_manager.extract_key_info(user_id, user_message, reply))

        return reply or "ขอโทษครับ ไม่สามารถสร้างคำตอบได้"
    except httpx.ConnectError:
        return "⚠️ ไม่สามารถเชื่อมต่อกับ Ollama ได้"
    except httpx.TimeoutException:
        return "⏳ Ollama ใช้เวลานานเกินไป กรุณาลองใหม่"
    except Exception as e:
        return f"❌ เกิดข้อผิดพลาด: {e}"


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
        "options": {"temperature": 0.7, "num_predict": -1},
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
                    "options":  {"temperature":0.5,"num_predict":512},
                })
                summaries.append(r.json()["message"]["content"].strip())
        except Exception as e:
            summaries.append(f"(ส่วนที่ {i} ล้มเหลว: {e})")

    combined = "\n\n".join(f"ส่วนที่ {i+1}: {s}" for i,s in enumerate(summaries))
    final    = f"สรุปภาพรวมของ '{filename}' ({len(chunks)} ส่วน):\n\n{combined}\n\nสรุปรวมทั้งหมดให้กระชับ"
    return await chat_with_ollama(user_id, session_id, final, source, background_tasks=background_tasks, model=model)


async def smart_summarize(user_id: str, session_id: str,
                           filename: str, content: str,
                           file_type: str, user_prompt: str = "",
                           source: str = "webchat",
                           background_tasks: BackgroundTasks = None,
                           model: str = None) -> str:
    if len(content) <= TURBOVEC_MIN_CHARS:
        prompt = (
            f"ไฟล์ชื่อ '{filename}' ({file_type}):\n\n{content}\n\n"
            "สรุปใจความสำคัญ ประเด็นหลัก และข้อมูลสำคัญให้กระชับครบถ้วน"
        )
        return await chat_with_ollama(user_id, session_id, prompt, source, background_tasks=background_tasks, model=model)

    query = (user_prompt or "").strip()
    if not query:
        query = f"สรุปใจความสำคัญ ประเด็นหลัก ข้อมูลสำคัญของไฟล์ {filename}"

    try:
        index_info = await message_turbovec.index_document(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            content=content,
            chunk_chars=TURBOVEC_CHUNK_CHARS,
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
        return await chat_with_ollama(user_id, session_id, prompt, source, background_tasks=background_tasks, model=model)

    if not matches:
        prompt = (
            f"ไฟล์ '{filename}' ({file_type}) ถูก index แล้ว แต่ไม่พบส่วนที่เกี่ยวข้องกับคำขอ: {query}\n"
            "ให้ตอบว่าต้องการคำถามที่เฉพาะเจาะจงขึ้น"
        )
        return await chat_with_ollama(user_id, session_id, prompt, source, background_tasks=background_tasks, model=model)

    context = "\n\n".join(
        f"[ส่วนที่ {m['chunk_index'] + 1} | score {m['score']:.3f}]\n{m['content']}"
        for m in matches
    )
    prompt = (
        f"ผู้ใช้อัปโหลดไฟล์ '{filename}' ({file_type}) ระบบได้ทำ MessageTurboVec index แล้ว "
        f"และดึงเฉพาะส่วนที่เกี่ยวข้องที่สุด {len(matches)} ส่วนมาให้ ไม่ได้ส่งทั้งไฟล์ให้โมเดล\n\n"
        f"คำขอของผู้ใช้: {query}\n\n"
        f"บริบทจากเอกสาร:\n{context}\n\n"
        "ตอบโดยอ้างอิงเฉพาะบริบทจากเอกสารด้านบน ถ้าข้อมูลไม่พอให้บอกตรง ๆ "
        "และสรุปให้กระชับเป็นภาษาไทย"
    )
    return await chat_with_ollama(user_id, session_id, prompt, source, background_tasks=background_tasks, model=model)


# ── Chat API ──────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest, request: Request, background_tasks: BackgroundTasks):
    # [แก้ไขแล้ว] เพิ่ม IDOR เช็คสิทธิ์
    if not _can_access_session(request, req.session_id):
        return JSONResponse({"error": "Unauthorized Access to Session"}, status_code=403)

    user = get_current_user(request)
    user_id = user["id"] if user else f"anon_{req.session_id}"

    if req.message.strip().lower() in ["/reset", "ลืมทุกอย่าง", "เริ่มใหม่"]:
        conversation_manager.clear(req.session_id)
        db.clear_memories(user_id, req.session_id)
        return JSONResponse({"reply": "🔄 ล้างประวัติและ memory แล้ว เริ่มต้นใหม่ได้เลยครับ!"})

    reply = await chat_with_ollama(user_id, req.session_id, req.message, background_tasks=background_tasks, model=req.model)
    return JSONResponse({"reply": reply})


@app.post("/upload-file")
async def upload_file(request: Request, session_id: str,
                       user_prompt: str = "", model: str = None, file: UploadFile = File(...),
                       background_tasks: BackgroundTasks = None):
    # [แก้ไขแล้ว] เพิ่ม IDOR เช็คสิทธิ์
    if not _can_access_session(request, session_id):
        return JSONResponse({"error": "Unauthorized Access to Session"}, status_code=403)

    user    = get_current_user(request)
    user_id = user["id"] if user else f"anon_{session_id}"
    data    = await file.read()
    result  = route_file(file.filename, data)

    if result["type"] == "image":
        reply = await analyze_image(user_id, session_id,
                                     result["content"], result["mime"],
                                     user_prompt or "วิเคราะห์รูปภาพนี้")
    else:
        reply = await smart_summarize(user_id, session_id,
                                       file.filename, result["content"],
                                       result["summary"], user_prompt,
                                       background_tasks=background_tasks,
                                       model=model)

    return JSONResponse({"reply": reply, "filename": file.filename,
                         "file_type": result["summary"]})


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