import os
import io
import httpx
import logging
import pypdf
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from conversation import ConversationManager
from database import db

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "gemma3:9b")
SYSTEM_PROMPT   = os.getenv("SYSTEM_PROMPT", (
    "คุณคือผู้ช่วย AI ที่ฉลาดและเป็นมิตร ตอบคำถามภาษาไทยได้อย่างชัดเจน "
    "กระชับ และเป็นประโยชน์ หากไม่แน่ใจให้บอกตรง ๆ "
    "เมื่อเขียนโค้ดให้ใส่ภาษาของโค้ดหลัง ``` เสมอ เช่น ```python"
))
PDF_MAX_CHARS = int(os.getenv("PDF_MAX_CHARS", "20000"))
WEB_PORT      = int(os.getenv("WEB_PORT", "8001"))

app = FastAPI(title="Gemma Web Chat")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
conversation_manager = ConversationManager(max_turns=20)


class ChatRequest(BaseModel):
    session_id: str
    message:    str

class RenameRequest(BaseModel):
    title: str


def extract_pdf_text(pdf_bytes: bytes, max_chars: int = 6000) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages, total = [], 0
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            pages.append(f"[หน้า {i+1}]\n{text}")
            total += len(text)
            if total >= max_chars:
                break
        full = "\n\n".join(pages)
        if len(full) > max_chars:
            full = full[:max_chars] + "\n...(ถูกตัดเนื่องจากยาวเกินไป)"
        return full or "(ไม่พบข้อความในไฟล์ PDF)"
    except Exception as e:
        return f"(อ่าน PDF ไม่ได้: {e})"


async def chat_with_ollama(session_id: str, user_message: str, source: str = "webchat") -> str:
    conversation_manager.add_message(session_id, "user", user_message, source)
    messages = conversation_manager.get_messages(session_id)
    payload = {
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


@app.post("/chat")
async def chat(req: ChatRequest):
    if req.message.strip().lower() in ["/reset", "ลืมทุกอย่าง", "เริ่มใหม่"]:
        conversation_manager.clear(req.session_id)
        return JSONResponse({"reply": "🔄 ล้างประวัติการสนทนาแล้ว เริ่มต้นใหม่ได้เลยครับ!"})
    reply = await chat_with_ollama(req.session_id, req.message, "webchat")
    return JSONResponse({"reply": reply})

@app.post("/upload-pdf")
async def upload_pdf(session_id: str, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"reply": "⚠️ รองรับเฉพาะไฟล์ .pdf เท่านั้นครับ"})
    pdf_bytes = await file.read()
    pdf_text  = extract_pdf_text(pdf_bytes, PDF_MAX_CHARS)
    prompt    = f"ไฟล์ PDF ชื่อ '{file.filename}':\n\n{pdf_text}\n\nกรุณาสรุปเนื้อหาสำคัญให้กระชับ"
    reply     = await chat_with_ollama(session_id, prompt, "webchat")
    return JSONResponse({"reply": reply, "filename": file.filename})

@app.get("/api/sessions")
async def get_sessions(source: str = None):
    return JSONResponse(db.get_sessions(source=source))

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
    return JSONResponse(db.search_messages(q))

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
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Gemma AI</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Thai:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d0f14;--sb:#111318;--surface:#151821;--surface2:#1c2030;
  --border:#252a3d;--accent:#6c8fff;--accent2:#a78bfa;--green:#4ade80;
  --red:#f87171;--yellow:#fbbf24;--text:#e2e8f8;--muted:#6b7280;
  --user-bg:#1e3564;--bot-bg:#161b2e;--code-bg:#0d1117;
  --font:'Noto Sans Thai',sans-serif;--mono:'JetBrains Mono',monospace;
  --sb-w:268px;--radius:14px;
}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;line-height:1.6;overflow:hidden}

/* ── Layout ── */
.app{display:flex;height:100dvh}

/* ── Sidebar ── */
.sb{width:var(--sb-w);min-width:var(--sb-w);background:var(--sb);border-right:1px solid var(--border);display:flex;flex-direction:column;transition:transform .25s ease}
.sb-top{padding:12px 10px 8px;display:flex;flex-direction:column;gap:6px}
.btn-new{display:flex;align-items:center;gap:9px;width:100%;padding:9px 12px;border-radius:10px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:var(--font);font-size:13px;font-weight:500;cursor:pointer;transition:background .15s,border-color .15s}
.btn-new:hover{background:var(--surface2);border-color:var(--accent)}
.btn-new .ico{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.search-wrap{position:relative}
.sb-search{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:9px;padding:7px 11px 7px 32px;color:var(--text);font-family:var(--font);font-size:12px;outline:none;transition:border-color .2s}
.sb-search:focus{border-color:var(--accent)}
.search-ico{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:13px;pointer-events:none}
.sb-section{padding:6px 10px 2px;font-size:10px;font-weight:600;color:var(--muted);letter-spacing:.8px;text-transform:uppercase}
.chat-list{flex:1;overflow-y:auto;padding:4px 6px 12px}
.chat-list::-webkit-scrollbar{width:3px}
.chat-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.chat-item{display:flex;align-items:center;gap:8px;padding:8px 8px;border-radius:9px;cursor:pointer;transition:background .12s;margin-bottom:1px;position:relative}
.chat-item:hover{background:var(--surface2)}
.chat-item.active{background:var(--surface2);outline:1px solid var(--border)}
.chat-item-ico{font-size:15px;flex-shrink:0;opacity:.8}
.chat-item-body{flex:1;min-width:0}
.chat-item-title{font-size:12.5px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text)}
.chat-item-sub{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.chat-actions{display:none;gap:2px;flex-shrink:0}
.chat-item:hover .chat-actions{display:flex}
.chat-item.active .chat-actions{display:flex}
.ico-btn{background:none;border:none;color:var(--muted);cursor:pointer;padding:3px 4px;border-radius:5px;font-size:12px;transition:color .15s,background .15s}
.ico-btn:hover{color:var(--text);background:var(--border)}
.ico-btn.del:hover{color:var(--red)}
.sb-empty{text-align:center;padding:32px 16px;color:var(--muted);font-size:12px}
.sb-foot{padding:10px 12px;border-top:1px solid var(--border);font-size:11px;color:var(--muted);display:flex;align-items:center;gap:6px}
.sb-foot .dot{width:6px;height:6px;border-radius:50%;background:var(--green)}

/* ── Main ── */
.main{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--bg)}

/* Topbar */
.topbar{display:flex;align-items:center;gap:10px;padding:0 20px;height:52px;background:var(--surface);border-bottom:1px solid var(--border);flex-shrink:0}
.menu-btn{display:none;background:none;border:none;color:var(--muted);cursor:pointer;font-size:18px;padding:4px}
.topbar-title{flex:1;font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.topbar-sub{font-size:11px;color:var(--muted)}

/* Messages */
.msgs{flex:1;overflow-y:auto;padding:28px 0;display:flex;flex-direction:column;gap:0;scroll-behavior:smooth}
.msgs::-webkit-scrollbar{width:4px}
.msgs::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}

/* Welcome */
.welcome{display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;padding:40px 24px;text-align:center;animation:fadeUp .3s ease}
.welcome-av{width:76px;height:76px;border-radius:22px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;font-size:36px;margin-bottom:20px;box-shadow:0 8px 32px rgba(108,143,255,.25)}
.welcome h2{font-size:24px;font-weight:600;margin-bottom:8px}
.welcome p{font-size:13px;color:var(--muted);line-height:1.8;max-width:360px}
.sugs{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:24px;max-width:480px}
.sug{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:8px 16px;border-radius:20px;font-size:12.5px;font-family:var(--font);cursor:pointer;transition:all .2s}
.sug:hover{border-color:var(--accent);color:var(--accent);transform:translateY(-2px);box-shadow:0 4px 12px rgba(108,143,255,.15)}

/* Message row */
.msg-row{padding:6px 24px;display:flex;gap:12px;max-width:860px;margin:0 auto;width:100%}
.msg-row.user{flex-direction:row-reverse}
.msg-av{width:32px;height:32px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-top:2px}
.msg-row.bot  .msg-av{background:linear-gradient(135deg,var(--accent),var(--accent2))}
.msg-row.user .msg-av{background:var(--user-bg);border:1px solid rgba(108,143,255,.3)}
.msg-body{max-width:72%;display:flex;flex-direction:column;gap:4px}
.msg-row.user .msg-body{align-items:flex-end}
.bubble{padding:11px 15px;border-radius:var(--radius);font-size:13.5px;line-height:1.75;word-break:break-word}
.msg-row.bot  .bubble{background:var(--bot-bg);border:1px solid var(--border);border-top-left-radius:4px}
.msg-row.user .bubble{background:var(--user-bg);border:1px solid rgba(108,143,255,.2);border-top-right-radius:4px}
.msg-time{font-size:10px;color:var(--muted);padding:0 4px}

/* Code */
.code-block{margin:8px 0;border-radius:10px;overflow:hidden;border:1px solid var(--border);background:var(--code-bg)}
.code-bar{display:flex;align-items:center;justify-content:space-between;padding:8px 14px;background:#161b22;border-bottom:1px solid var(--border)}
.code-lang{font-family:var(--mono);font-size:11px;color:var(--accent);font-weight:500}
.code-btns{display:flex;gap:6px}
.code-btn{background:var(--surface2);border:1px solid var(--border);color:var(--muted);padding:3px 10px;border-radius:5px;font-size:11px;cursor:pointer;font-family:var(--font);transition:all .15s}
.code-btn:hover{background:var(--accent);border-color:var(--accent);color:white}
.code-btn.ok{background:var(--green);border-color:var(--green);color:#000}
pre{padding:16px;overflow-x:auto;font-family:var(--mono);font-size:12.5px;line-height:1.65;color:#c9d1d9;margin:0}
pre::-webkit-scrollbar{height:3px}
pre::-webkit-scrollbar-thumb{background:var(--border)}

/* PDF badge */
.pdf-badge{display:inline-flex;align-items:center;gap:6px;background:rgba(167,139,250,.1);border:1px solid rgba(167,139,250,.3);border-radius:8px;padding:5px 11px;font-size:12px;color:var(--accent2);margin-bottom:8px}

/* Typing */
.typing-bubble{display:flex;align-items:center;gap:5px;padding:13px 16px}
.tdot{width:6px;height:6px;border-radius:50%;background:var(--accent);animation:tdot 1.3s infinite}
.tdot:nth-child(2){animation-delay:.2s}.tdot:nth-child(3){animation-delay:.4s}
@keyframes tdot{0%,80%,100%{transform:translateY(0);opacity:.3}40%{transform:translateY(-7px);opacity:1}}

/* Input area */
.input-area{padding:12px 24px 20px;background:var(--bg);flex-shrink:0}
.input-inner{max-width:860px;margin:0 auto}
.toolbar{display:flex;gap:8px;margin-bottom:8px}
.tool-btn{display:flex;align-items:center;gap:5px;background:var(--surface);border:1px solid var(--border);color:var(--muted);padding:5px 12px;border-radius:8px;font-size:12px;font-family:var(--font);cursor:pointer;transition:all .2s}
.tool-btn:hover{border-color:var(--accent2);color:var(--accent2)}
#fileInput{display:none}
.pdf-chip{display:none;align-items:center;gap:7px;background:rgba(167,139,250,.1);border:1px solid rgba(167,139,250,.3);border-radius:8px;padding:5px 11px;font-size:12px;color:var(--accent2)}
.pdf-chip.show{display:flex}
.pdf-chip button{background:none;border:none;color:var(--muted);cursor:pointer;font-size:14px;line-height:1}
.input-box{display:flex;gap:10px;align-items:flex-end;background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:10px 12px;transition:border-color .2s;box-shadow:0 2px 16px rgba(0,0,0,.2)}
.input-box:focus-within{border-color:var(--accent);box-shadow:0 2px 24px rgba(108,143,255,.12)}
textarea{flex:1;background:none;border:none;outline:none;color:var(--text);font-family:var(--font);font-size:14px;line-height:1.6;resize:none;max-height:160px;min-height:24px}
textarea::placeholder{color:var(--muted)}
.send-btn{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:opacity .2s,transform .1s;box-shadow:0 2px 8px rgba(108,143,255,.3)}
.send-btn:hover{opacity:.88}.send-btn:active{transform:scale(.93)}.send-btn:disabled{opacity:.35;cursor:not-allowed}
.send-btn svg{width:17px;height:17px;fill:white}
.hint{text-align:center;font-size:11px;color:var(--muted);margin-top:8px;opacity:.7}

/* Modal */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:200;align-items:center;justify-content:center;backdrop-filter:blur(4px)}
.modal-bg.show{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:24px;width:360px;box-shadow:0 24px 64px rgba(0,0,0,.4)}
.modal h3{font-size:15px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.modal input{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:9px;padding:10px 13px;color:var(--text);font-family:var(--font);font-size:14px;outline:none;margin-bottom:16px}
.modal input:focus{border-color:var(--accent)}
.modal-footer{display:flex;gap:8px;justify-content:flex-end}
.btn-cancel{background:none;border:1px solid var(--border);color:var(--muted);padding:8px 18px;border-radius:9px;font-family:var(--font);font-size:13px;cursor:pointer;transition:all .15s}
.btn-cancel:hover{border-color:var(--text);color:var(--text)}
.btn-ok{background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;color:white;padding:8px 18px;border-radius:9px;font-family:var(--font);font-size:13px;cursor:pointer;transition:opacity .15s}
.btn-ok:hover{opacity:.88}

/* Animations */
@keyframes fadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.animate{animation:fadeUp .22s ease}

/* Mobile */
@media(max-width:680px){
  .sb{position:fixed;left:0;top:0;bottom:0;z-index:100;transform:translateX(-100%);box-shadow:4px 0 24px rgba(0,0,0,.4)}
  .sb.open{transform:translateX(0)}
  .menu-btn{display:flex}
  .sb-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:99}
  .sb-overlay.show{display:block}
  .msg-row{padding:6px 14px}
  .input-area{padding:10px 14px 16px}
}
</style>
</head>
<body>
<div class="app">

<!-- Sidebar -->
<div class="sb" id="sb">
  <div class="sb-top">
    <button class="btn-new" onclick="newChat()">
      <div class="ico">✏️</div>
      <span>แชทใหม่</span>
    </button>
    <div class="search-wrap">
      <span class="search-ico">🔍</span>
      <input class="sb-search" id="sbSearch" placeholder="ค้นหาแชท..." oninput="onSearch(this.value)">
    </div>
  </div>
  <div class="sb-section">การสนทนา</div>
  <div class="chat-list" id="chatList"></div>
  <div class="sb-foot">
    <div class="dot"></div>
    <span>Gemma AI · Local</span>
  </div>
</div>
<div class="sb-overlay" id="sbOverlay" onclick="closeSb()"></div>

<!-- Main -->
<div class="main">
  <div class="topbar">
    <button class="menu-btn" onclick="toggleSb()">☰</button>
    <div>
      <div class="topbar-title" id="topTitle">Gemma AI</div>
      <div class="topbar-sub" id="topSub">Local AI · พร้อมใช้งาน</div>
    </div>
  </div>

  <div class="msgs" id="msgs"></div>

  <div class="input-area">
    <div class="input-inner">
      <div class="toolbar">
        <button class="tool-btn" onclick="document.getElementById('fileInput').click()">📄 อัปโหลด PDF</button>
        <input type="file" id="fileInput" accept=".pdf" onchange="onFile(this)">
        <div class="pdf-chip" id="pdfChip">
          <span>📄</span><span id="pdfName"></span>
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
      <div class="hint">Enter ส่ง · Shift+Enter ขึ้นบรรทัดใหม่</div>
    </div>
  </div>
</div>
</div>

<!-- Rename Modal -->
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
// ── State ──────────────────────────────────────────────────────────────────
let curSession  = null;
let sessions    = [];
let allSessions = [];
let busy        = false;
let selFile     = null;
let renameId    = null;

const msgsEl  = document.getElementById('msgs');
const inpEl   = document.getElementById('inp');
const sendBtn = document.getElementById('sendBtn');

// ── Boot ───────────────────────────────────────────────────────────────────
(async () => {
  await loadSessions();
  if (sessions.length) {
    await switchChat(sessions[0].id);
  } else {
    newChat();
  }
  inpEl.focus();
})();

// ── Sessions ───────────────────────────────────────────────────────────────
async function loadSessions() {
  try {
    const r   = await fetch('/api/sessions?source=webchat');
    allSessions = await r.json();
    sessions    = [...allSessions];
    renderList(sessions);
  } catch(e) { console.error(e); }
}

function renderList(list) {
  const el = document.getElementById('chatList');
  if (!list.length) {
    el.innerHTML = '<div class="sb-empty">ยังไม่มีการสนทนา<br>กด "แชทใหม่" เพื่อเริ่ม</div>';
    return;
  }
  el.innerHTML = list.map(s => {
    const title   = s.title || genTitle(s.last_user_msg) || 'การสนทนาใหม่';
    const preview = (s.last_user_msg || '').slice(0, 42) || 'ยังไม่มีข้อความ';
    const active  = s.id === curSession ? 'active' : '';
    return `
    <div class="chat-item ${active}" id="ci-${s.id}" onclick="switchChat('${s.id}')">
      <div class="chat-item-ico">💬</div>
      <div class="chat-item-body">
        <div class="chat-item-title">${esc(title)}</div>
        <div class="chat-item-sub">${esc(preview)}</div>
      </div>
      <div class="chat-actions">
        <button class="ico-btn" title="เปลี่ยนชื่อ"
          onclick="event.stopPropagation();openRename('${s.id}','${esc(title)}')">✏️</button>
        <button class="ico-btn del" title="ลบ"
          onclick="event.stopPropagation();delChat('${s.id}')">🗑</button>
      </div>
    </div>`;
  }).join('');
}

function genTitle(msg) {
  if (!msg) return null;
  return msg.length > 30 ? msg.slice(0, 30) + '…' : msg;
}

function newChat() {
  curSession = 'sess_' + Math.random().toString(36).slice(2, 11);
  document.getElementById('topTitle').textContent = 'การสนทนาใหม่';
  document.getElementById('topSub').textContent   = 'Local AI · พร้อมใช้งาน';
  showWelcome();
  document.querySelectorAll('.chat-item').forEach(e => e.classList.remove('active'));
  closeSb();
  inpEl.focus();
}

async function switchChat(id) {
  curSession = id;
  closeSb();

  // active state
  document.querySelectorAll('.chat-item').forEach(e => e.classList.remove('active'));
  document.getElementById(`ci-${id}`)?.classList.add('active');

  // fetch messages
  msgsEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">กำลังโหลด...</div>';
  try {
    const r    = await fetch(`/api/sessions/${id}`);
    const data = await r.json();

    const title = data.title || genTitle(data.messages?.find(m=>m.role==='user')?.content) || 'การสนทนา';
    document.getElementById('topTitle').textContent = title;
    document.getElementById('topSub').textContent   = `${data.messages?.length || 0} ข้อความ`;

    msgsEl.innerHTML = '';
    if (!data.messages?.length) { showWelcome(); return; }

    data.messages.forEach(m => {
      addBubble(m.role === 'user' ? 'user' : 'bot', m.content, null, m.created_at);
    });
    scrollBot();
  } catch(e) { msgsEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--red)">โหลดไม่ได้</div>'; }
  inpEl.focus();
}

async function delChat(id) {
  if (!confirm('ลบการสนทนานี้?')) return;
  await fetch(`/api/sessions/${id}`, { method: 'DELETE' });
  allSessions = allSessions.filter(s => s.id !== id);
  sessions    = sessions.filter(s => s.id !== id);
  renderList(sessions);
  if (curSession === id) {
    sessions.length ? await switchChat(sessions[0].id) : newChat();
  }
}

// ── Search ─────────────────────────────────────────────────────────────────
let searchT = null;
function onSearch(v) {
  clearTimeout(searchT);
  if (!v.trim()) { sessions = [...allSessions]; renderList(sessions); return; }
  searchT = setTimeout(async () => {
    const r    = await fetch(`/api/search?q=${encodeURIComponent(v)}`);
    const data = await r.json();
    const ids  = [...new Set(data.map(m => m.session_id))];
    sessions   = allSessions.filter(s => ids.includes(s.id));
    renderList(sessions);
  }, 350);
}

// ── Rename ─────────────────────────────────────────────────────────────────
function openRename(id, title) {
  renameId = id;
  document.getElementById('renameInp').value = title;
  document.getElementById('modalBg').classList.add('show');
  setTimeout(() => document.getElementById('renameInp').select(), 80);
}
function closeModal() {
  renameId = null;
  document.getElementById('modalBg').classList.remove('show');
}
async function doRename() {
  const title = document.getElementById('renameInp').value.trim();
  if (!title || !renameId) return;
  await fetch(`/api/sessions/${renameId}/rename`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ title })
  });
  // update local
  [allSessions, sessions].forEach(arr => {
    const s = arr.find(x => x.id === renameId);
    if (s) s.title = title;
  });
  if (curSession === renameId)
    document.getElementById('topTitle').textContent = title;
  renderList(sessions);
  closeModal();
}
document.getElementById('renameInp').addEventListener('keydown', e => {
  if (e.key === 'Enter') doRename();
  if (e.key === 'Escape') closeModal();
});
document.getElementById('modalBg').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});

// ── Sidebar mobile ─────────────────────────────────────────────────────────
function toggleSb() {
  document.getElementById('sb').classList.toggle('open');
  document.getElementById('sbOverlay').classList.toggle('show');
}
function closeSb() {
  document.getElementById('sb').classList.remove('open');
  document.getElementById('sbOverlay').classList.remove('show');
}

// ── Welcome ────────────────────────────────────────────────────────────────
function showWelcome() {
  msgsEl.innerHTML = `
  <div class="welcome">
    <div class="welcome-av">🤖</div>
    <h2>สวัสดีครับ!</h2>
    <p>ผมคือ Gemma AI ขับเคลื่อนด้วย Local AI<br>พิมพ์ถามได้เลย หรืออัปโหลด PDF ให้ผมสรุปให้ครับ</p>
    <div class="sugs">
      <button class="sug" onclick="useSug(this)">อธิบาย Machine Learning ให้เข้าใจง่าย</button>
      <button class="sug" onclick="useSug(this)">เขียน Python อ่านไฟล์ CSV</button>
      <button class="sug" onclick="useSug(this)">แปลภาษาอังกฤษให้หน่อย</button>
      <button class="sug" onclick="useSug(this)">ช่วยเขียน email อย่างเป็นทางการ</button>
    </div>
  </div>`;
}

// ── Render helpers ─────────────────────────────────────────────────────────
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function langExt(l){
  const m={python:'py',javascript:'js',typescript:'ts',html:'html',css:'css',java:'java',cpp:'cpp',c:'c',go:'go',rust:'rs',bash:'sh',shell:'sh',sql:'sql',json:'json',yaml:'yaml',php:'php',ruby:'rb',kotlin:'kt',swift:'swift'};
  return m[l.toLowerCase()]||'txt';
}
function renderMd(text) {
  const frag = document.createDocumentFragment();
  const parts = text.split(/(```[\s\S]*?```)/g);
  parts.forEach(p => {
    const m = p.match(/^```(\w*)\n?([\s\S]*?)```$/);
    if (m) {
      const lang = m[1]||'text', code = m[2].trim(), ext = langExt(lang);
      const blk = document.createElement('div');
      blk.className = 'code-block';
      blk.innerHTML = `
        <div class="code-bar">
          <span class="code-lang">${lang}</span>
          <div class="code-btns">
            <button class="code-btn" onclick="copyCode(this)">คัดลอก</button>
            <button class="code-btn" onclick="dlCode(this,'${ext}')">⬇ ดาวน์โหลด</button>
          </div>
        </div>
        <pre><code></code></pre>`;
      blk.querySelector('code').textContent = code;
      frag.appendChild(blk);
    } else if (p.trim()) {
      const sp = document.createElement('span');
      sp.style.whiteSpace = 'pre-wrap';
      sp.textContent = p;
      frag.appendChild(sp);
    }
  });
  return frag;
}
function copyCode(btn) {
  const code = btn.closest('.code-block').querySelector('code').textContent;
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = '✓ คัดลอกแล้ว'; btn.classList.add('ok');
    setTimeout(() => { btn.textContent = 'คัดลอก'; btn.classList.remove('ok'); }, 2000);
  });
}
function dlCode(btn, ext) {
  const code = btn.closest('.code-block').querySelector('code').textContent;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([code], {type:'text/plain'}));
  a.download = `code_${Date.now()}.${ext}`; a.click();
}
function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString('th-TH', {hour:'2-digit', minute:'2-digit'});
}

function addBubble(role, text, pdfName, time) {
  document.querySelector('.welcome')?.remove();
  const row = document.createElement('div');
  row.className = `msg-row ${role} animate`;

  const av = document.createElement('div');
  av.className = 'msg-av';
  av.textContent = role === 'bot' ? '🤖' : '👤';

  const body = document.createElement('div');
  body.className = 'msg-body';

  const bubble = document.createElement('div');
  bubble.className = role === 'typing' ? 'bubble typing-bubble' : 'bubble';

  if (role === 'typing') {
    bubble.innerHTML = '<div class="tdot"></div><div class="tdot"></div><div class="tdot"></div>';
  } else {
    if (pdfName) {
      const badge = document.createElement('div');
      badge.className = 'pdf-badge';
      badge.innerHTML = `📄 <strong>${esc(pdfName)}</strong>`;
      bubble.appendChild(badge);
    }
    bubble.appendChild(renderMd(text));
  }

  const timeEl = document.createElement('div');
  timeEl.className = 'msg-time';
  timeEl.textContent = fmtTime(time) || '';

  body.appendChild(bubble);
  if (time) body.appendChild(timeEl);
  row.appendChild(av);
  row.appendChild(body);
  msgsEl.appendChild(row);
  scrollBot();
  return row;
}

function addTyping() {
  document.querySelector('.welcome')?.remove();
  const row = document.createElement('div');
  row.className = 'msg-row bot animate';
  row.id = 'typing';
  const av = document.createElement('div');
  av.className = 'msg-av'; av.textContent = '🤖';
  const body = document.createElement('div'); body.className = 'msg-body';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = '<div class="tdot"></div><div class="tdot"></div><div class="tdot"></div>';
  body.appendChild(bubble); row.appendChild(av); row.appendChild(body);
  msgsEl.appendChild(row); scrollBot();
}
function rmTyping() { document.getElementById('typing')?.remove(); }
function scrollBot() { msgsEl.scrollTo({top: msgsEl.scrollHeight, behavior:'smooth'}); }

// ── File ───────────────────────────────────────────────────────────────────
function onFile(input) {
  if (!input.files[0]) return;
  selFile = input.files[0];
  document.getElementById('pdfName').textContent = selFile.name;
  document.getElementById('pdfChip').classList.add('show');
}
function clearFile() {
  selFile = null;
  document.getElementById('fileInput').value = '';
  document.getElementById('pdfChip').classList.remove('show');
}

// ── Send ───────────────────────────────────────────────────────────────────
function resize(el) { el.style.height='auto'; el.style.height=Math.min(el.scrollHeight,160)+'px'; }
function onKey(e) { if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();} }
function useSug(btn) { inpEl.value=btn.textContent; send(); }

async function send() {
  const text = inpEl.value.trim();
  if ((!text && !selFile) || busy) return;
  busy = true; sendBtn.disabled = true;
  inpEl.value = ''; inpEl.style.height = 'auto';

  const now = new Date().toISOString();

  if (selFile) {
    const file = selFile; clearFile();
    addBubble('user', text || `ช่วยสรุปไฟล์ ${file.name} ให้หน่อยครับ`, null, now);
    addTyping();
    const form = new FormData(); form.append('file', file);
    try {
      const r    = await fetch(`/upload-pdf?session_id=${curSession}`, {method:'POST', body:form});
      const data = await r.json();
      rmTyping(); addBubble('bot', data.reply, data.filename, new Date().toISOString());
    } catch { rmTyping(); addBubble('bot', '❌ อัปโหลด PDF ไม่ได้ครับ'); }

  } else {
    if (text.toLowerCase() === '/reset' || text === 'ลืมทุกอย่าง' || text === 'เริ่มใหม่') {
      addBubble('user', text, null, now); addTyping();
      const r    = await fetch('/chat', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({session_id: curSession, message: text})});
      const data = await r.json();
      rmTyping(); addBubble('bot', data.reply, null, new Date().toISOString());
    } else {
      addBubble('user', text, null, now); addTyping();
      try {
        const r    = await fetch('/chat', {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({session_id: curSession, message: text})});
        const data = await r.json();
        rmTyping(); addBubble('bot', data.reply, null, new Date().toISOString());
      } catch { rmTyping(); addBubble('bot', '❌ ไม่สามารถเชื่อมต่อ server ได้'); }
    }
  }

  // refresh sidebar
  await loadSessions();
  // re-highlight active
  document.querySelectorAll('.chat-item').forEach(e => e.classList.remove('active'));
  document.getElementById(`ci-${curSession}`)?.classList.add('active');

  busy = false; sendBtn.disabled = false; inpEl.focus();
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn, socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "localhost"
    print(f"""
╔══════════════════════════════════════════╗
║         Gemma Web Chat  v5               ║
╠══════════════════════════════════════════╣
║  Chat:    http://localhost:{WEB_PORT}         ║
║  Network: http://{local_ip}:{WEB_PORT}   ║
╚══════════════════════════════════════════╝
    """)
    uvicorn.run("webchat:app", host="0.0.0.0", port=WEB_PORT, reload=False)