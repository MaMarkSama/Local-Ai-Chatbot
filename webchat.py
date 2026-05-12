# -*- coding: utf-8 -*-
import os
import io
import sys
import httpx

# Fix Windows encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import logging
import base64
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from conversation import ConversationManager
from database import db
from file_processor import route_file

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "gemma3:9b")
OLLAMA_VISION   = os.getenv("OLLAMA_VISION_MODEL", OLLAMA_MODEL)  # gemma4 รองรับ vision
SYSTEM_PROMPT   = os.getenv("SYSTEM_PROMPT", (
    "คุณคือผู้ช่วย AI ที่ฉลาดและเป็นมิตร ตอบคำถามภาษาไทยได้อย่างชัดเจน "
    "กระชับ และเป็นประโยชน์ หากไม่แน่ใจให้บอกตรง ๆ "
    "เมื่อเขียนโค้ดให้ใส่ภาษาของโค้ดหลัง ``` เสมอ เช่น ```python"
))
WEB_PORT = int(os.getenv("WEB_PORT", "8001"))

app = FastAPI(title="Gemma Web Chat")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
conversation_manager = ConversationManager(max_turns=20)


class ChatRequest(BaseModel):
    session_id: str
    message:    str

class RenameRequest(BaseModel):
    title: str


# ── Ollama: Text chat ─────────────────────────────────────────────────────
async def chat_with_ollama(session_id: str, user_message: str, source: str = "webchat") -> str:
    conversation_manager.add_message(session_id, "user", user_message, source)
    messages = conversation_manager.get_messages(session_id)
    payload  = {
        "model":    OLLAMA_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "stream":   False,
        "options":  {"temperature": 0.7, "num_predict": -1, "repeat_penalty": 1.1},
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
            reply = r.json()["message"]["content"].strip()
        conversation_manager.add_message(session_id, "assistant", reply, source)
        return reply or "ขอโทษครับ ไม่สามารถสร้างคำตอบได้"
    except httpx.ConnectError:
        return "⚠️ ไม่สามารถเชื่อมต่อกับ Ollama ได้"
    except httpx.TimeoutException:
        return "⏳ Ollama ใช้เวลานานเกินไป กรุณาลองใหม่"
    except Exception as e:
        return f"❌ เกิดข้อผิดพลาด: {e}"


# ── Ollama: Vision (image analysis) ──────────────────────────────────────
async def analyze_image(session_id: str, b64: str, mime: str,
                         user_prompt: str, source: str = "webchat") -> str:
    prompt = user_prompt or "กรุณาอธิบายรูปภาพนี้อย่างละเอียด"
    # Ollama format: ใช้ "images" array ใน message (ไม่ใช่ image_url)
    payload = {
        "model":  OLLAMA_VISION,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt, "images": [b64]},
        ],
        "stream":  False,
        "options": {"temperature": 0.7, "num_predict": -1},
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
            reply = r.json()["message"]["content"].strip()
        # บันทึกลง history แบบ text
        conversation_manager.add_message(session_id, "user", f"[รูปภาพ] {prompt}", source)
        conversation_manager.add_message(session_id, "assistant", reply, source)
        return reply or "ไม่สามารถวิเคราะห์รูปภาพได้"
    except Exception as e:
        return f"❌ วิเคราะห์รูปภาพไม่ได้: {e}"


# ── Ollama: Smart summarize (chunked for large docs) ──────────────────────
async def smart_summarize(session_id: str, filename: str,
                           content: str, file_type: str, source: str = "webchat") -> str:
    CHUNK = 6000
    if len(content) <= CHUNK:
        # สั้นพอ สรุปตรงๆ
        prompt = (
            f"ไฟล์ชื่อ '{filename}' ({file_type}) มีเนื้อหาดังนี้:\n\n{content}\n\n"
            "กรุณาสรุปใจความสำคัญ ประเด็นหลัก และข้อมูลสำคัญให้กระชับและครบถ้วน"
        )
        return await chat_with_ollama(session_id, prompt, source)

    # ยาวมาก → แบ่ง chunk แล้วสรุปแต่ละ chunk ก่อน
    chunks = [content[i:i+CHUNK] for i in range(0, len(content), CHUNK)]
    logger.info(f"[{session_id}] Summarizing {filename} in {len(chunks)} chunks")

    chunk_summaries = []
    for i, chunk in enumerate(chunks, 1):
        prompt = (
            f"นี่คือส่วนที่ {i}/{len(chunks)} ของไฟล์ '{filename}':\n\n{chunk}\n\n"
            "สรุปประเด็นสำคัญของส่วนนี้ให้กระชับ"
        )
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json={
                    "model":    OLLAMA_MODEL,
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                 {"role": "user", "content": prompt}],
                    "stream":   False,
                    "options":  {"temperature": 0.5, "num_predict": 512},
                })
                r.raise_for_status()
                chunk_summaries.append(r.json()["message"]["content"].strip())
        except Exception as e:
            chunk_summaries.append(f"(สรุปส่วนที่ {i} ไม่ได้: {e})")

    # สรุปรวม
    combined = "\n\n".join([f"ส่วนที่ {i+1}: {s}" for i, s in enumerate(chunk_summaries)])
    final_prompt = (
        f"ต่อไปนี้คือสรุปแต่ละส่วนของไฟล์ '{filename}' ({file_type}, {len(chunks)} ส่วน):\n\n"
        f"{combined}\n\n"
        "กรุณาสรุปภาพรวมทั้งหมดให้กระชับ ครบถ้วน และเป็นระบบ "
        "โดยระบุประเด็นสำคัญ ข้อมูลหลัก และสิ่งที่ควรรู้"
    )
    final = await chat_with_ollama(session_id, final_prompt, source)
    return final


# ── API Endpoints ─────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    if req.message.strip().lower() in ["/reset", "ลืมทุกอย่าง", "เริ่มใหม่"]:
        conversation_manager.clear(req.session_id)
        return JSONResponse({"reply": "🔄 ล้างประวัติการสนทนาแล้ว เริ่มต้นใหม่ได้เลยครับ!"})
    reply = await chat_with_ollama(req.session_id, req.message)
    return JSONResponse({"reply": reply})


@app.post("/upload-file")
async def upload_file(session_id: str, user_prompt: str = "",
                       file: UploadFile = File(...)):
    data     = await file.read()
    filename = file.filename
    logger.info(f"[{session_id}] Upload: {filename} ({len(data)} bytes)")

    result = route_file(filename, data)

    if result["type"] == "image":
        reply = await analyze_image(
            session_id, result["content"], result["mime"],
            user_prompt or "กรุณาวิเคราะห์และอธิบายรูปภาพนี้"
        )
        return JSONResponse({"reply": reply, "filename": filename,
                             "file_type": "image", "summary": result["summary"]})
    else:
        reply = await smart_summarize(
            session_id, filename, result["content"], result["summary"]
        )
        return JSONResponse({"reply": reply, "filename": filename,
                             "file_type": result["summary"], "summary": result["summary"]})


# ── Session API ────────────────────────────────────────────────────────────
@app.get("/api/sessions")
async def get_sessions(source: str = None):
    return JSONResponse(db.get_sessions(source=source, limit=100))

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    msgs = db.get_session_messages(session_id)
    s    = next((x for x in db.get_sessions() if x["id"] == session_id), {})
    return JSONResponse({"session_id": session_id, "title": s.get("title"), "messages": msgs})

@app.post("/api/sessions/{session_id}/rename")
async def rename_session(session_id: str, req: RenameRequest):
    db.rename_session(session_id, req.title)
    return JSONResponse({"status": "ok"})

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    db.delete_session(session_id)
    conversation_manager.clear(session_id)
    return JSONResponse({"status": "ok"})

@app.get("/api/search")
async def search(q: str):
    return JSONResponse(db.search_messages(q, limit=30))

@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r      = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            return {"status": "ok", "ollama": "online", "model": OLLAMA_MODEL,
                    "model_loaded": any(OLLAMA_MODEL in m for m in models)}
    except Exception:
        return {"status": "ok", "ollama": "offline"}

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=HTML)


HTML = r"""<!DOCTYPE html>
<html lang="th" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Gemma AI</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Thai:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
[data-theme="dark"]{
  --bg:#0d0f14;--sb:#111318;--surface:#151821;--surface2:#1c2030;
  --border:#252a3d;--text:#e2e8f8;--muted:#6b7280;--muted2:#9ca3b0;
  --user-bg:#1e3564;--bot-bg:#161b2e;--code-bg:#0d1117;--code-bar:#161b22;
  --shadow:rgba(0,0,0,.35);--input-bg:#151821;
}
[data-theme="light"]{
  --bg:#f7f7f5;--sb:#ffffff;--surface:#ffffff;--surface2:#f0efeb;
  --border:#e0ddd8;--text:#1a1a1a;--muted:#6b7280;--muted2:#9ca3af;
  --user-bg:#2563eb;--bot-bg:#ffffff;--code-bg:#1e1e2e;--code-bar:#2a2a3d;
  --shadow:rgba(0,0,0,.07);--input-bg:#ffffff;
}
:root{--accent:#6c8fff;--accent2:#a78bfa;--green:#22c55e;--red:#ef4444;--font:'Noto Sans Thai',sans-serif;--mono:'JetBrains Mono',monospace;--sb-w:268px;--radius:14px}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;line-height:1.6;overflow:hidden;transition:background .2s,color .2s}
.app{display:flex;height:100dvh}

/* Sidebar */
.sb{width:var(--sb-w);min-width:var(--sb-w);background:var(--sb);border-right:1px solid var(--border);display:flex;flex-direction:column;transition:transform .25s,background .2s}
.sb-top{padding:12px 10px 8px;display:flex;flex-direction:column;gap:6px}
.btn-new{display:flex;align-items:center;gap:9px;width:100%;padding:9px 12px;border-radius:10px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:var(--font);font-size:13px;font-weight:500;cursor:pointer;transition:background .15s,border-color .15s}
.btn-new:hover{background:var(--surface2);border-color:var(--accent)}
.btn-new .ico{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.search-wrap{position:relative}
.sb-search{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:9px;padding:7px 11px 7px 32px;color:var(--text);font-family:var(--font);font-size:12px;outline:none;transition:border-color .2s,background .2s}
.sb-search:focus{border-color:var(--accent)}
.search-ico{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:13px;pointer-events:none}
.sb-section{padding:6px 10px 2px;font-size:10px;font-weight:600;color:var(--muted);letter-spacing:.8px;text-transform:uppercase}
.chat-list{flex:1;overflow-y:auto;padding:4px 6px 12px}
.chat-list::-webkit-scrollbar{width:3px}
.chat-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.chat-item{display:flex;align-items:center;gap:8px;padding:8px;border-radius:9px;cursor:pointer;transition:background .12s;margin-bottom:1px}
.chat-item:hover,.chat-item.active{background:var(--surface2)}
.chat-item.active{outline:1px solid var(--border)}
.chat-item-ico{font-size:15px;flex-shrink:0;opacity:.8}
.chat-item-body{flex:1;min-width:0}
.chat-item-title{font-size:12.5px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text)}
.chat-item-sub{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.chat-actions{display:none;gap:2px;flex-shrink:0}
.chat-item:hover .chat-actions,.chat-item.active .chat-actions{display:flex}
.ico-btn{background:none;border:none;color:var(--muted);cursor:pointer;padding:3px 4px;border-radius:5px;font-size:12px;transition:color .15s}
.ico-btn:hover{color:var(--text)}
.ico-btn.del:hover{color:var(--red)}
.sb-empty{text-align:center;padding:32px 16px;color:var(--muted);font-size:12px}
.sb-foot{padding:10px 12px;border-top:1px solid var(--border);font-size:11px;color:var(--muted);display:flex;align-items:center;gap:8px}
.sb-foot .dot{width:6px;height:6px;border-radius:50%;background:var(--green);flex-shrink:0}

/* Main */
.main{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--bg);transition:background .2s}
.topbar{display:flex;align-items:center;gap:10px;padding:0 20px;height:52px;background:var(--surface);border-bottom:1px solid var(--border);flex-shrink:0;transition:background .2s}
.menu-btn{display:none;background:none;border:none;color:var(--muted);cursor:pointer;font-size:18px;padding:4px}
.topbar-info{flex:1;min-width:0}
.topbar-title{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.topbar-sub{font-size:11px;color:var(--muted)}
.topbar-actions{display:flex;align-items:center;gap:6px;flex-shrink:0}
.theme-btn{background:var(--surface2);border:1px solid var(--border);color:var(--text);width:34px;height:34px;border-radius:9px;cursor:pointer;font-size:16px;display:flex;align-items:center;justify-content:center;transition:all .2s}
.theme-btn:hover{border-color:var(--accent)}

/* Messages */
.msgs{flex:1;overflow-y:auto;padding:24px 0 12px;display:flex;flex-direction:column;scroll-behavior:smooth}
.msgs::-webkit-scrollbar{width:4px}
.msgs::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}

/* Welcome */
.welcome{display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;padding:40px 24px;text-align:center;animation:fadeUp .3s ease}
.welcome-av{width:76px;height:76px;border-radius:22px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;font-size:36px;margin-bottom:20px;box-shadow:0 8px 32px rgba(108,143,255,.25)}
.welcome h2{font-size:22px;font-weight:600;margin-bottom:8px}
.welcome p{font-size:13px;color:var(--muted);line-height:1.85;max-width:380px}
.sugs{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:22px;max-width:520px}
.sug{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:8px 16px;border-radius:20px;font-size:12.5px;font-family:var(--font);cursor:pointer;transition:all .2s}
.sug:hover{border-color:var(--accent);color:var(--accent);transform:translateY(-2px)}

/* Bubbles */
.msg-row{padding:6px 24px;display:flex;gap:12px;max-width:860px;margin:0 auto;width:100%;animation:fadeUp .2s ease}
.msg-row.user{flex-direction:row-reverse}
.msg-av{width:32px;height:32px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-top:2px}
.msg-row.bot  .msg-av{background:linear-gradient(135deg,var(--accent),var(--accent2))}
.msg-row.user .msg-av{background:var(--user-bg)}
.msg-body{max-width:74%;display:flex;flex-direction:column;gap:3px}
.msg-row.user .msg-body{align-items:flex-end}
.bubble{padding:11px 15px;border-radius:var(--radius);font-size:13.5px;line-height:1.78;word-break:break-word;transition:background .2s}
.msg-row.bot  .bubble{background:var(--bot-bg);border:1px solid var(--border);border-top-left-radius:4px}
.msg-row.user .bubble{background:var(--user-bg);color:#fff;border-top-right-radius:4px}
.msg-time{font-size:10px;color:var(--muted);padding:0 4px}

/* File badge */
.file-badge{display:inline-flex;align-items:center;gap:6px;border-radius:8px;padding:5px 11px;font-size:12px;margin-bottom:8px;border:1px solid}
.file-badge.pdf{background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.3);color:#f87171}
.file-badge.csv{background:rgba(34,197,94,.08);border-color:rgba(34,197,94,.3);color:#4ade80}
.file-badge.xml{background:rgba(251,191,36,.08);border-color:rgba(251,191,36,.3);color:#fbbf24}
.file-badge.doc{background:rgba(59,130,246,.08);border-color:rgba(59,130,246,.3);color:#60a5fa}
.file-badge.img{background:rgba(167,139,250,.08);border-color:rgba(167,139,250,.3);color:var(--accent2)}
.file-badge.other{background:rgba(108,143,255,.08);border-color:rgba(108,143,255,.3);color:var(--accent)}

/* Img preview */
.img-preview{max-width:280px;max-height:200px;border-radius:10px;border:1px solid var(--border);margin-bottom:8px;display:block}

/* Code */
.code-block{margin:8px 0;border-radius:10px;overflow:hidden;border:1px solid var(--border)}
.code-bar{display:flex;align-items:center;justify-content:space-between;padding:7px 14px;background:var(--code-bar);border-bottom:1px solid var(--border)}
.code-lang{font-family:var(--mono);font-size:11px;color:var(--accent);font-weight:500}
.code-btns{display:flex;gap:5px}
.code-btn{background:var(--surface2);border:1px solid var(--border);color:var(--muted2);padding:3px 10px;border-radius:5px;font-size:11px;cursor:pointer;font-family:var(--font);transition:all .15s}
.code-btn:hover{background:var(--accent);border-color:var(--accent);color:#fff}
.code-btn.ok{background:var(--green);border-color:var(--green);color:#fff}
pre{background:var(--code-bg);padding:16px;overflow-x:auto;font-family:var(--mono);font-size:12.5px;line-height:1.65;color:#c9d1d9}

/* Typing */
.tdot{width:6px;height:6px;border-radius:50%;background:var(--accent);display:inline-block;animation:tdot 1.3s infinite}
.tdot:nth-child(2){animation-delay:.2s}.tdot:nth-child(3){animation-delay:.4s}
@keyframes tdot{0%,80%,100%{transform:translateY(0);opacity:.3}40%{transform:translateY(-7px);opacity:1}}

/* Input */
.input-area{padding:10px 24px 20px;background:var(--bg);flex-shrink:0;transition:background .2s}
.input-inner{max-width:860px;margin:0 auto}
.toolbar{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}
.tool-btn{display:flex;align-items:center;gap:5px;background:var(--surface);border:1px solid var(--border);color:var(--muted);padding:5px 11px;border-radius:8px;font-size:12px;font-family:var(--font);cursor:pointer;transition:all .2s;white-space:nowrap}
.tool-btn:hover{border-color:var(--accent);color:var(--accent)}
#fileInput{display:none}
.file-chip{display:none;align-items:center;gap:7px;border-radius:8px;padding:5px 11px;font-size:12px}
.file-chip.show{display:flex}
.file-chip button{background:none;border:none;color:var(--muted);cursor:pointer;font-size:14px}
.input-box{display:flex;gap:10px;align-items:flex-end;background:var(--input-bg);border:1px solid var(--border);border-radius:14px;padding:10px 12px;transition:border-color .2s,box-shadow .2s;box-shadow:0 2px 16px var(--shadow)}
.input-box:focus-within{border-color:var(--accent);box-shadow:0 2px 24px rgba(108,143,255,.12)}
textarea{flex:1;background:none;border:none;outline:none;color:var(--text);font-family:var(--font);font-size:14px;line-height:1.6;resize:none;max-height:160px;min-height:24px}
textarea::placeholder{color:var(--muted)}
.send-btn{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:opacity .2s,transform .1s}
.send-btn:hover{opacity:.88}.send-btn:active{transform:scale(.93)}.send-btn:disabled{opacity:.35;cursor:not-allowed}
.send-btn svg{width:17px;height:17px;fill:white}
.hint{text-align:center;font-size:11px;color:var(--muted);margin-top:7px;opacity:.7}

/* Modal */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;align-items:center;justify-content:center;backdrop-filter:blur(4px)}
.modal-bg.show{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:24px;width:360px;box-shadow:0 24px 64px var(--shadow)}
.modal h3{font-size:15px;font-weight:600;margin-bottom:16px}
.modal input{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:9px;padding:10px 13px;color:var(--text);font-family:var(--font);font-size:14px;outline:none;margin-bottom:16px;transition:border-color .2s}
.modal input:focus{border-color:var(--accent)}
.modal-footer{display:flex;gap:8px;justify-content:flex-end}
.btn-cancel{background:none;border:1px solid var(--border);color:var(--muted);padding:8px 18px;border-radius:9px;font-family:var(--font);font-size:13px;cursor:pointer}
.btn-cancel:hover{border-color:var(--text);color:var(--text)}
.btn-ok{background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;color:#fff;padding:8px 18px;border-radius:9px;font-family:var(--font);font-size:13px;cursor:pointer}

/* Progress */
.progress-msg{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);padding:4px 0}
.spinner{width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;flex-shrink:0}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes fadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}

/* Mobile */
@media(max-width:680px){
  .sb{position:fixed;left:0;top:0;bottom:0;z-index:100;transform:translateX(-100%);box-shadow:4px 0 24px var(--shadow)}
  .sb.open{transform:translateX(0)}
  .menu-btn{display:flex}
  .sb-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:99}
  .sb-overlay.show{display:block}
  .msg-row{padding:6px 14px}
  .input-area{padding:10px 14px 16px}
}
</style>
</head>
<body>
<div class="app">

<div class="sb" id="sb">
  <div class="sb-top">
    <button class="btn-new" onclick="newChat()">
      <div class="ico">✏️</div><span>แชทใหม่</span>
    </button>
    <div class="search-wrap">
      <span class="search-ico">🔍</span>
      <input class="sb-search" placeholder="ค้นหาแชท..." oninput="onSearch(this.value)">
    </div>
  </div>
  <div class="sb-section">การสนทนา</div>
  <div class="chat-list" id="chatList"></div>
  <div class="sb-foot"><div class="dot"></div><span>Gemma AI · Local</span></div>
</div>
<div class="sb-overlay" id="sbOverlay" onclick="closeSb()"></div>

<div class="main">
  <div class="topbar">
    <button class="menu-btn" onclick="toggleSb()">☰</button>
    <div class="topbar-info">
      <div class="topbar-title" id="topTitle">Gemma AI</div>
      <div class="topbar-sub" id="topSub">Local AI · พร้อมใช้งาน</div>
    </div>
    <div class="topbar-actions">
      <button class="theme-btn" id="themeBtn" onclick="toggleTheme()" title="เปลี่ยนธีม">🌙</button>
    </div>
  </div>

  <div class="msgs" id="msgs"></div>

  <div class="input-area">
    <div class="input-inner">
      <div class="toolbar">
        <button class="tool-btn" onclick="triggerFile()">📎 แนบไฟล์</button>
        <input type="file" id="fileInput" accept=".pdf,.csv,.xml,.docx,.doc,.xlsx,.xls,.txt,.md,.json,.yaml,.yml,.jpg,.jpeg,.png,.gif,.webp" onchange="onFile(this)">
        <div class="file-chip show" id="fileChip" style="display:none">
          <span id="fileIcon">📄</span><span id="fileName"></span>
          <button onclick="clearFile()">✕</button>
        </div>
      </div>
      <div class="input-box">
        <textarea id="inp" placeholder="ส่งข้อความถึง Gemma..." rows="1"
          onkeydown="onKey(event)" oninput="resize(this)"></textarea>
        <button class="send-btn" id="sendBtn" onclick="send()">
          <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
        </button>
      </div>
      <div class="hint">Enter ส่ง · Shift+Enter ขึ้นบรรทัดใหม่ · รองรับ PDF, CSV, XML, DOCX, XLSX, รูปภาพ</div>
    </div>
  </div>
</div>
</div>

<div class="modal-bg" id="modalBg">
  <div class="modal">
    <h3>✏️ เปลี่ยนชื่อการสนทนา</h3>
    <input type="text" id="renameInp" placeholder="ชื่อการสนทนา...">
    <div class="modal-footer">
      <button class="btn-cancel" onclick="closeModal()">ยกเลิก</button>
      <button class="btn-ok" onclick="doRename()">บันทึก</button>
    </div>
  </div>
</div>

<script>
// ── Theme ──────────────────────────────────────────────────────────────────
const THEME_KEY = 'gemma_theme';
function applyTheme(t){
  document.documentElement.setAttribute('data-theme',t);
  document.getElementById('themeBtn').textContent = t==='dark'?'🌙':'☀️';
  localStorage.setItem(THEME_KEY,t);
}
function toggleTheme(){
  applyTheme(document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark');
}
applyTheme(localStorage.getItem(THEME_KEY)||'dark');

// ── State ──────────────────────────────────────────────────────────────────
let curSession=null, sessions=[], allSessions=[], busy=false, selFile=null, renameId=null;
const msgsEl=document.getElementById('msgs'), inpEl=document.getElementById('inp'), sendBtn=document.getElementById('sendBtn');

// ── Boot ───────────────────────────────────────────────────────────────────
(async()=>{
  await loadSessions();
  sessions.length ? await switchChat(sessions[0].id) : newChat();
  inpEl.focus();
})();

// ── Sessions ───────────────────────────────────────────────────────────────
async function loadSessions(){
  try{
    const r=await fetch('/api/sessions?source=webchat');
    allSessions=await r.json(); sessions=[...allSessions]; renderList(sessions);
  }catch(e){console.error(e)}
}

function renderList(list){
  const el=document.getElementById('chatList');
  if(!list.length){el.innerHTML='<div class="sb-empty">ยังไม่มีการสนทนา<br>กด "แชทใหม่" เพื่อเริ่ม</div>';return}
  el.innerHTML=list.map(s=>{
    const title=s.title||genTitle(s.last_user_msg)||'การสนทนาใหม่';
    const preview=(s.last_user_msg||'').slice(0,40)||'ยังไม่มีข้อความ';
    const active=s.id===curSession?'active':'';
    return `<div class="chat-item ${active}" id="ci-${s.id}" onclick="switchChat('${s.id}')">
      <div class="chat-item-ico">💬</div>
      <div class="chat-item-body">
        <div class="chat-item-title">${esc(title)}</div>
        <div class="chat-item-sub">${esc(preview)}</div>
      </div>
      <div class="chat-actions">
        <button class="ico-btn" onclick="event.stopPropagation();openRename('${s.id}','${esc(title).replace(/'/g,"\\'")}')">✏️</button>
        <button class="ico-btn del" onclick="event.stopPropagation();delChat('${s.id}')">🗑</button>
      </div>
    </div>`;
  }).join('');
}

function genTitle(msg){if(!msg)return null;return msg.length>32?msg.slice(0,32)+'…':msg}

function newChat(){
  curSession='sess_'+Math.random().toString(36).slice(2,11);
  document.getElementById('topTitle').textContent='การสนทนาใหม่';
  document.getElementById('topSub').textContent='Local AI · พร้อมใช้งาน';
  showWelcome();
  document.querySelectorAll('.chat-item').forEach(e=>e.classList.remove('active'));
  closeSb(); inpEl.focus();
}

async function switchChat(id){
  curSession=id; closeSb();
  document.querySelectorAll('.chat-item').forEach(e=>e.classList.remove('active'));
  document.getElementById(`ci-${id}`)?.classList.add('active');
  msgsEl.innerHTML='<div style="text-align:center;padding:48px;color:var(--muted)">กำลังโหลด...</div>';
  try{
    const r=await fetch(`/api/sessions/${id}`);
    const data=await r.json();
    const title=data.title||genTitle(data.messages?.find(m=>m.role==='user')?.content)||'การสนทนา';
    document.getElementById('topTitle').textContent=title;
    document.getElementById('topSub').textContent=`${data.messages?.length||0} ข้อความ`;
    msgsEl.innerHTML='';
    if(!data.messages?.length){showWelcome();return}
    data.messages.forEach(m=>addBubble(m.role==='user'?'user':'bot',m.content,null,m.created_at));
    scrollBot();
  }catch{msgsEl.innerHTML='<div style="text-align:center;padding:40px;color:var(--red)">โหลดไม่ได้</div>'}
  inpEl.focus();
}

async function delChat(id){
  if(!confirm('ลบการสนทนานี้?'))return;
  await fetch(`/api/sessions/${id}`,{method:'DELETE'});
  allSessions=allSessions.filter(s=>s.id!==id);
  sessions=sessions.filter(s=>s.id!==id);
  renderList(sessions);
  if(curSession===id) sessions.length?await switchChat(sessions[0].id):newChat();
}

let searchT=null;
function onSearch(v){
  clearTimeout(searchT);
  if(!v.trim()){sessions=[...allSessions];renderList(sessions);return}
  searchT=setTimeout(async()=>{
    const r=await fetch(`/api/search?q=${encodeURIComponent(v)}`);
    const d=await r.json();
    const ids=[...new Set(d.map(m=>m.session_id))];
    sessions=allSessions.filter(s=>ids.includes(s.id));
    renderList(sessions);
  },350);
}

function openRename(id,title){
  renameId=id;
  document.getElementById('renameInp').value=title;
  document.getElementById('modalBg').classList.add('show');
  setTimeout(()=>document.getElementById('renameInp').select(),80);
}
function closeModal(){renameId=null;document.getElementById('modalBg').classList.remove('show')}
async function doRename(){
  const title=document.getElementById('renameInp').value.trim();
  if(!title||!renameId)return;
  await fetch(`/api/sessions/${renameId}/rename`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})});
  [allSessions,sessions].forEach(arr=>{const s=arr.find(x=>x.id===renameId);if(s)s.title=title});
  if(curSession===renameId)document.getElementById('topTitle').textContent=title;
  renderList(sessions);closeModal();
}
document.getElementById('renameInp').addEventListener('keydown',e=>{if(e.key==='Enter')doRename();if(e.key==='Escape')closeModal()});
document.getElementById('modalBg').addEventListener('click',e=>{if(e.target===e.currentTarget)closeModal()});

function toggleSb(){document.getElementById('sb').classList.toggle('open');document.getElementById('sbOverlay').classList.toggle('show')}
function closeSb(){document.getElementById('sb').classList.remove('open');document.getElementById('sbOverlay').classList.remove('show')}

// ── Welcome ────────────────────────────────────────────────────────────────
function showWelcome(){
  msgsEl.innerHTML=`<div class="welcome">
    <div class="welcome-av">🤖</div>
    <h2>สวัสดีครับ!</h2>
    <p>ผมคือ Gemma AI ขับเคลื่อนด้วย Local AI<br>พิมพ์ถามได้เลย หรือแนบไฟล์เพื่อให้วิเคราะห์</p>
    <div class="sugs">
      <button class="sug" onclick="useSug(this)">อธิบาย Machine Learning ให้เข้าใจง่าย</button>
      <button class="sug" onclick="useSug(this)">เขียน Python อ่านไฟล์ CSV</button>
      <button class="sug" onclick="useSug(this)">แปลภาษาอังกฤษให้หน่อย</button>
      <button class="sug" onclick="useSug(this)">ช่วยเขียน email อย่างเป็นทางการ</button>
    </div>
  </div>`;
}

// ── Render ─────────────────────────────────────────────────────────────────
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function langExt(l){const m={python:'py',javascript:'js',typescript:'ts',html:'html',css:'css',java:'java',cpp:'cpp',c:'c',go:'go',rust:'rs',bash:'sh',shell:'sh',sql:'sql',json:'json',yaml:'yaml',php:'php',ruby:'rb'};return m[l.toLowerCase()]||'txt'}

function renderMd(text){
  const frag=document.createDocumentFragment();
  text.split(/(```[\s\S]*?```)/g).forEach(p=>{
    const m=p.match(/^```(\w*)\n?([\s\S]*?)```$/);
    if(m){
      const lang=m[1]||'text',code=m[2].trim(),ext=langExt(lang);
      const blk=document.createElement('div');blk.className='code-block';
      blk.innerHTML=`<div class="code-bar"><span class="code-lang">${lang}</span><div class="code-btns"><button class="code-btn" onclick="cpCode(this)">คัดลอก</button><button class="code-btn" onclick="dlCode(this,'${ext}')">⬇ ดาวน์โหลด</button></div></div><pre><code></code></pre>`;
      blk.querySelector('code').textContent=code;frag.appendChild(blk);
    } else if(p.trim()){
      const sp=document.createElement('span');sp.style.whiteSpace='pre-wrap';sp.textContent=p;frag.appendChild(sp);
    }
  });
  return frag;
}
function cpCode(btn){
  navigator.clipboard.writeText(btn.closest('.code-block').querySelector('code').textContent).then(()=>{
    btn.textContent='✓ คัดลอกแล้ว';btn.classList.add('ok');
    setTimeout(()=>{btn.textContent='คัดลอก';btn.classList.remove('ok')},2000);
  });
}
function dlCode(btn,ext){
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([btn.closest('.code-block').querySelector('code').textContent],{type:'text/plain'}));
  a.download=`code_${Date.now()}.${ext}`;a.click();
}
function fmtTime(iso){if(!iso)return'';return new Date(iso).toLocaleTimeString('th-TH',{hour:'2-digit',minute:'2-digit'})}

function getFileBadgeClass(summary){
  if(!summary)return'other';
  const s=summary.toLowerCase();
  if(s.includes('pdf'))return'pdf';
  if(s.includes('csv'))return'csv';
  if(s.includes('xml'))return'xml';
  if(s.includes('word')||s.includes('doc'))return'doc';
  if(s.includes('excel')||s.includes('xlsx'))return'doc';
  if(s.includes('รูป')||s.includes('image'))return'img';
  return'other';
}
function getFileIcon(summary){
  if(!summary)return'📄';
  const s=summary.toLowerCase();
  if(s.includes('pdf'))return'📕';
  if(s.includes('csv'))return'📊';
  if(s.includes('xml'))return'📋';
  if(s.includes('word')||s.includes('doc'))return'📝';
  if(s.includes('excel')||s.includes('xlsx'))return'📗';
  if(s.includes('รูป')||s.includes('image'))return'🖼️';
  return'📄';
}

function addBubble(role, text, fileMeta, time){
  document.querySelector('.welcome')?.remove();
  const row=document.createElement('div');row.className=`msg-row ${role}`;
  const av=document.createElement('div');av.className='msg-av';av.textContent=role==='bot'?'🤖':'👤';
  const body=document.createElement('div');body.className='msg-body';
  const bubble=document.createElement('div');bubble.className='bubble';

  if(fileMeta){
    const cls=getFileBadgeClass(fileMeta.type);
    const ico=getFileIcon(fileMeta.type);
    const badge=document.createElement('div');
    badge.className=`file-badge ${cls}`;
    badge.innerHTML=`${ico} <strong>${esc(fileMeta.name)}</strong> <span style="opacity:.7">${esc(fileMeta.type)}</span>`;
    bubble.appendChild(badge);

    // ถ้าเป็นรูปให้แสดง preview
    if(fileMeta.preview){
      const img=document.createElement('img');
      img.className='img-preview';
      img.src=fileMeta.preview;
      img.alt=fileMeta.name;
      bubble.appendChild(img);
    }
  }

  bubble.appendChild(renderMd(text));
  const timeEl=document.createElement('div');timeEl.className='msg-time';timeEl.textContent=fmtTime(time)||'';
  body.appendChild(bubble);if(time)body.appendChild(timeEl);
  row.appendChild(av);row.appendChild(body);msgsEl.appendChild(row);scrollBot();
}

function addTypingMsg(msg=''){
  document.querySelector('.welcome')?.remove();
  const row=document.createElement('div');row.className='msg-row bot';row.id='typing';
  const av=document.createElement('div');av.className='msg-av';av.textContent='🤖';
  const body=document.createElement('div');body.className='msg-body';
  const bubble=document.createElement('div');bubble.className='bubble';
  if(msg){
    bubble.innerHTML=`<div class="progress-msg"><div class="spinner"></div><span>${esc(msg)}</span></div>`;
  } else {
    bubble.innerHTML='<div class="tdot"></div><div class="tdot"></div><div class="tdot"></div>';
  }
  body.appendChild(bubble);row.appendChild(av);row.appendChild(body);msgsEl.appendChild(row);scrollBot();
}
function rmTyping(){document.getElementById('typing')?.remove()}
function scrollBot(){msgsEl.scrollTo({top:msgsEl.scrollHeight,behavior:'smooth'})}

// ── File ───────────────────────────────────────────────────────────────────
const IMG_EXTS=['.jpg','.jpeg','.png','.gif','.webp','.bmp'];
let filePreviewUrl=null;

function triggerFile(){document.getElementById('fileInput').click()}

function onFile(input){
  if(!input.files[0])return;
  selFile=input.files[0];
  const ext='.'+selFile.name.split('.').pop().toLowerCase();
  const isImg=IMG_EXTS.includes(ext);
  document.getElementById('fileIcon').textContent=isImg?'🖼️':'📄';
  document.getElementById('fileName').textContent=selFile.name;
  const chip=document.getElementById('fileChip');
  chip.style.display='flex';
  chip.className=`file-chip show ${isImg?'img':'other'}`;
  chip.style.background=isImg?'rgba(167,139,250,.1)':'rgba(108,143,255,.1)';
  chip.style.border=`1px solid ${isImg?'rgba(167,139,250,.3)':'rgba(108,143,255,.3)'}`;
  chip.style.color=isImg?'var(--accent2)':'var(--accent)';

  // สร้าง preview URL สำหรับรูป
  if(isImg && filePreviewUrl){URL.revokeObjectURL(filePreviewUrl);filePreviewUrl=null}
  if(isImg){filePreviewUrl=URL.createObjectURL(selFile)}
}

function clearFile(){
  selFile=null;
  document.getElementById('fileInput').value='';
  document.getElementById('fileChip').style.display='none';
  if(filePreviewUrl){URL.revokeObjectURL(filePreviewUrl);filePreviewUrl=null}
}

// ── Send ───────────────────────────────────────────────────────────────────
function resize(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,160)+'px'}
function onKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}}
function useSug(btn){inpEl.value=btn.textContent;send()}

async function send(){
  const text=inpEl.value.trim();
  if((!text&&!selFile)||busy)return;
  busy=true;sendBtn.disabled=true;
  inpEl.value='';inpEl.style.height='auto';
  const now=new Date().toISOString();

  if(selFile){
    const file=selFile;
    const ext='.'+file.name.split('.').pop().toLowerCase();
    const isImg=IMG_EXTS.includes(ext);
    const preview=isImg&&filePreviewUrl?filePreviewUrl:null;
    clearFile();

    const userText=text||(isImg?`วิเคราะห์รูปภาพ ${file.name} ให้หน่อย`:`สรุปไฟล์ ${file.name} ให้หน่อย`);
    addBubble('user',userText,{name:file.name,type:isImg?'รูปภาพ':file.name.split('.').pop().toUpperCase(),preview},now);

    const progressMsg=isImg?'กำลังวิเคราะห์รูปภาพ...':'กำลังอ่านและวิเคราะห์ไฟล์...';
    addTypingMsg(progressMsg);

    const form=new FormData();
    form.append('file',file);
    form.append('user_prompt',text);
    try{
      const r=await fetch(`/upload-file?session_id=${curSession}&user_prompt=${encodeURIComponent(text)}`,{method:'POST',body:form});
      const d=await r.json();
      rmTyping();
      addBubble('bot',d.reply,null,new Date().toISOString());
    }catch(e){
      rmTyping();addBubble('bot',`❌ เกิดข้อผิดพลาด: ${e.message}`);
    }

  } else {
    addBubble('user',text,null,now);addTypingMsg();
    try{
      const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({session_id:curSession,message:text})});
      const d=await r.json();rmTyping();addBubble('bot',d.reply,null,new Date().toISOString());
    }catch{rmTyping();addBubble('bot','❌ ไม่สามารถเชื่อมต่อ server ได้')}
  }

  await loadSessions();
  document.querySelectorAll('.chat-item').forEach(e=>e.classList.remove('active'));
  document.getElementById(`ci-${curSession}`)?.classList.add('active');
  busy=false;sendBtn.disabled=false;inpEl.focus();
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn, socket
    import sys, io
    # Fix Windows encoding
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        s.connect(("8.8.8.8",80))
        local_ip=s.getsockname()[0];s.close()
    except Exception:
        local_ip="localhost"
    print(f"Gemma Web Chat v6")
    print(f"  Chat:    http://localhost:{WEB_PORT}")
    print(f"  Network: http://{local_ip}:{WEB_PORT}")
    uvicorn.run("webchat:app", host="0.0.0.0", port=WEB_PORT, reload=False)