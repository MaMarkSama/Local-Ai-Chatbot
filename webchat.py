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

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "gemma3:9b")
SYSTEM_PROMPT   = os.getenv("SYSTEM_PROMPT", (
    "คุณคือผู้ช่วย AI ที่ฉลาดและเป็นมิตร ตอบคำถามภาษาไทยได้อย่างชัดเจน "
    "กระชับ และเป็นประโยชน์ หากไม่แน่ใจให้บอกตรง ๆ "
    "เมื่อเขียนโค้ดให้ใส่ภาษาของโค้ดหลัง ``` เสมอ เช่น ```python หรือ ```javascript"
))
PDF_MAX_CHARS = int(os.getenv("PDF_MAX_CHARS", "6000"))
WEB_PORT      = int(os.getenv("WEB_PORT", "8001"))

# ── App ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Gemma Web Chat v2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

conversation_manager = ConversationManager(max_turns=20)


class ChatRequest(BaseModel):
    session_id: str
    message:    str


# ── PDF Extract ───────────────────────────────────────────────────────────
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


# ── Ollama ────────────────────────────────────────────────────────────────
async def chat_with_ollama(session_id: str, user_message: str) -> str:
    conversation_manager.add_message(session_id, "user", user_message)
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
        conversation_manager.add_message(session_id, "assistant", reply)
        return reply or "ขอโทษครับ ไม่สามารถสร้างคำตอบได้ ลองใหม่อีกครั้งนะครับ"
    except httpx.ConnectError:
        return "⚠️ ไม่สามารถเชื่อมต่อกับ Ollama ได้"
    except httpx.TimeoutException:
        return "⏳ Ollama ใช้เวลานานเกินไป กรุณาลองใหม่"
    except Exception as e:
        return f"❌ เกิดข้อผิดพลาด: {e}"


# ── Endpoints ─────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    if req.message.strip().lower() in ["/reset", "ลืมทุกอย่าง", "เริ่มใหม่"]:
        conversation_manager.clear(req.session_id)
        return JSONResponse({"reply": "🔄 ล้างประวัติการสนทนาแล้ว เริ่มต้นใหม่ได้เลยครับ!"})
    reply = await chat_with_ollama(req.session_id, req.message)
    return JSONResponse({"reply": reply})


@app.post("/upload-pdf")
async def upload_pdf(session_id: str, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"reply": "⚠️ รองรับเฉพาะไฟล์ .pdf เท่านั้นครับ"})
    pdf_bytes = await file.read()
    pdf_text  = extract_pdf_text(pdf_bytes, PDF_MAX_CHARS)
    logger.info(f"[{session_id}] PDF: {file.filename} ({len(pdf_text)} chars)")
    prompt = (
        f"ไฟล์ PDF ชื่อ '{file.filename}' มีเนื้อหาดังนี้:\n\n{pdf_text}\n\n"
        f"กรุณาสรุปเนื้อหาสำคัญให้กระชับและเข้าใจง่าย"
    )
    reply = await chat_with_ollama(session_id, prompt)
    return JSONResponse({"reply": reply, "filename": file.filename})


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
    return HTMLResponse(content=HTML_PAGE)


# ── HTML UI ───────────────────────────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Gemma AI Chat</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Thai:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg:       #0d0f14;
    --surface:  #151821;
    --surface2: #1c2030;
    --border:   #2a2f45;
    --accent:   #6c8fff;
    --accent2:  #a78bfa;
    --green:    #4ade80;
    --text:     #e2e8f8;
    --muted:    #8891aa;
    --user-bg:  #1e3a5f;
    --bot-bg:   #1c2030;
    --code-bg:  #0d1117;
    --radius:   16px;
    --font:     'Noto Sans Thai', sans-serif;
    --mono:     'JetBrains Mono', monospace;
  }
  html, body { height: 100%; background: var(--bg); color: var(--text); font-family: var(--font); font-size: 15px; line-height: 1.6; overflow: hidden; }

  .app { display: flex; flex-direction: column; height: 100dvh; max-width: 800px; margin: 0 auto; }

  /* Header */
  .header { display: flex; align-items: center; gap: 12px; padding: 14px 18px; background: var(--surface); border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .avatar { width: 38px; height: 38px; border-radius: 11px; background: linear-gradient(135deg, var(--accent), var(--accent2)); display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
  .header-info h1 { font-size: 15px; font-weight: 600; }
  .status { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .header-actions { margin-left: auto; display: flex; gap: 8px; }
  .btn-sm { background: none; border: 1px solid var(--border); color: var(--muted); padding: 5px 12px; border-radius: 8px; font-size: 12px; font-family: var(--font); cursor: pointer; transition: all .2s; }
  .btn-sm:hover { border-color: var(--accent); color: var(--accent); }

  /* Messages */
  .messages { flex: 1; overflow-y: auto; padding: 18px 14px; display: flex; flex-direction: column; gap: 14px; scroll-behavior: smooth; }
  .messages::-webkit-scrollbar { width: 4px; }
  .messages::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  /* Welcome */
  .welcome { text-align: center; padding: 40px 20px; color: var(--muted); }
  .welcome .big-av { width: 68px; height: 68px; border-radius: 18px; background: linear-gradient(135deg, var(--accent), var(--accent2)); display: flex; align-items: center; justify-content: center; font-size: 32px; margin: 0 auto 18px; }
  .welcome h2 { font-size: 20px; font-weight: 600; color: var(--text); margin-bottom: 6px; }
  .welcome p { font-size: 13px; line-height: 1.9; }
  .suggestions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 20px; }
  .sug { background: var(--surface2); border: 1px solid var(--border); color: var(--text); padding: 7px 14px; border-radius: 18px; font-size: 13px; font-family: var(--font); cursor: pointer; transition: all .2s; }
  .sug:hover { border-color: var(--accent); color: var(--accent); transform: translateY(-1px); }

  /* Bubbles */
  .msg { display: flex; gap: 9px; animation: fadeUp .22s ease; max-width: 88%; }
  @keyframes fadeUp { from{opacity:0;transform:translateY(7px)} to{opacity:1;transform:translateY(0)} }
  .msg.user { align-self: flex-end; flex-direction: row-reverse; }
  .msg-av { width: 30px; height: 30px; border-radius: 9px; display: flex; align-items: center; justify-content: center; font-size: 15px; flex-shrink: 0; margin-top: 3px; }
  .msg.bot  .msg-av { background: linear-gradient(135deg, var(--accent), var(--accent2)); }
  .msg.user .msg-av { background: var(--user-bg); }
  .bubble { padding: 10px 14px; border-radius: var(--radius); font-size: 14px; line-height: 1.75; word-break: break-word; }
  .msg.bot  .bubble { background: var(--bot-bg); border: 1px solid var(--border); border-top-left-radius: 4px; }
  .msg.user .bubble { background: var(--user-bg); border-top-right-radius: 4px; }

  /* Code block */
  .code-block { margin: 8px 0; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); }
  .code-header { display: flex; align-items: center; justify-content: space-between; background: #161b22; padding: 7px 12px; font-family: var(--mono); font-size: 12px; color: var(--muted); }
  .code-header span { color: var(--accent); font-weight: 500; }
  .code-actions { display: flex; gap: 6px; }
  .btn-code { background: var(--surface2); border: 1px solid var(--border); color: var(--text); padding: 3px 10px; border-radius: 5px; font-size: 11px; cursor: pointer; font-family: var(--font); transition: all .15s; }
  .btn-code:hover { background: var(--accent); border-color: var(--accent); color: white; }
  .btn-code.copied { background: var(--green); border-color: var(--green); color: #000; }
  pre { background: var(--code-bg); padding: 14px; overflow-x: auto; font-family: var(--mono); font-size: 13px; line-height: 1.6; color: #c9d1d9; }
  pre::-webkit-scrollbar { height: 4px; }
  pre::-webkit-scrollbar-thumb { background: var(--border); }

  /* Typing */
  .typing .bubble { display: flex; align-items: center; gap: 5px; padding: 13px 16px; }
  .tdot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); animation: bounce 1.2s infinite; }
  .tdot:nth-child(2){animation-delay:.2s} .tdot:nth-child(3){animation-delay:.4s}
  @keyframes bounce { 0%,80%,100%{transform:translateY(0);opacity:.4} 40%{transform:translateY(-6px);opacity:1} }

  /* PDF badge */
  .pdf-badge { display: inline-flex; align-items: center; gap: 6px; background: #1a1a2e; border: 1px solid #3d3d6b; border-radius: 8px; padding: 6px 12px; font-size: 13px; color: var(--accent2); margin-bottom: 8px; }

  /* Input area */
  .input-area { padding: 10px 14px 18px; background: var(--surface); border-top: 1px solid var(--border); flex-shrink: 0; }
  .toolbar { display: flex; gap: 8px; margin-bottom: 8px; }
  .btn-upload { display: flex; align-items: center; gap: 6px; background: var(--surface2); border: 1px solid var(--border); color: var(--muted); padding: 6px 12px; border-radius: 8px; font-size: 12px; font-family: var(--font); cursor: pointer; transition: all .2s; }
  .btn-upload:hover { border-color: var(--accent2); color: var(--accent2); }
  #fileInput { display: none; }
  .pdf-preview { display: none; align-items: center; gap: 8px; background: #1a1a2e; border: 1px solid #3d3d6b; border-radius: 8px; padding: 6px 12px; font-size: 12px; color: var(--accent2); }
  .pdf-preview.show { display: flex; }
  .pdf-preview button { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 15px; padding: 0 2px; }
  .input-wrap { display: flex; gap: 9px; align-items: flex-end; background: var(--surface2); border: 1px solid var(--border); border-radius: 13px; padding: 9px 11px; transition: border-color .2s; }
  .input-wrap:focus-within { border-color: var(--accent); }
  textarea { flex: 1; background: none; border: none; outline: none; color: var(--text); font-family: var(--font); font-size: 14px; line-height: 1.6; resize: none; max-height: 120px; min-height: 22px; }
  textarea::placeholder { color: var(--muted); }
  .send-btn { width: 34px; height: 34px; border-radius: 9px; background: linear-gradient(135deg, var(--accent), var(--accent2)); border: none; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; transition: opacity .2s, transform .1s; }
  .send-btn:hover { opacity: .85; }
  .send-btn:active { transform: scale(.94); }
  .send-btn:disabled { opacity: .4; cursor: not-allowed; }
  .send-btn svg { width: 17px; height: 17px; fill: white; }
  .hint { text-align: center; font-size: 11px; color: var(--muted); margin-top: 7px; }
</style>
</head>
<body>
<div class="app">
  <div class="header">
    <div class="avatar">🤖</div>
    <div class="header-info">
      <h1>Gemma AI</h1>
      <div class="status"><span class="dot"></span>Online · Local AI</div>
    </div>
    <div class="header-actions">
      <button class="btn-sm" onclick="resetChat()">เริ่มใหม่</button>
    </div>
  </div>

  <div class="messages" id="messages">
    <div class="welcome" id="welcome">
      <div class="big-av">🤖</div>
      <h2>สวัสดีครับ!</h2>
      <p>ผมคือ Gemma AI ขับเคลื่อนด้วย Local AI<br>ถามอะไรก็ได้ หรืออัปโหลด PDF ให้ผมสรุปให้ครับ</p>
      <div class="suggestions">
        <button class="sug" onclick="useSug(this)">แนะนำตัวหน่อย</button>
        <button class="sug" onclick="useSug(this)">เขียน Python อ่านไฟล์ CSV</button>
        <button class="sug" onclick="useSug(this)">อธิบาย API คืออะไร</button>
        <button class="sug" onclick="useSug(this)">แปลภาษาอังกฤษให้หน่อย</button>
      </div>
    </div>
  </div>

  <div class="input-area">
    <div class="toolbar">
      <button class="btn-upload" onclick="document.getElementById('fileInput').click()">
        📄 อัปโหลด PDF
      </button>
      <input type="file" id="fileInput" accept=".pdf" onchange="onFileSelect(this)">
      <div class="pdf-preview" id="pdfPreview">
        <span>📄</span>
        <span id="pdfName"></span>
        <button onclick="clearFile()">✕</button>
      </div>
    </div>
    <div class="input-wrap">
      <textarea id="input" placeholder="พิมพ์ข้อความ..." rows="1"
        onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
      <button class="send-btn" id="sendBtn" onclick="sendMessage()">
        <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
      </button>
    </div>
    <div class="hint">Enter ส่ง · Shift+Enter ขึ้นบรรทัดใหม่</div>
  </div>
</div>

<script>
const SESSION_ID = 'sess_' + Math.random().toString(36).slice(2,10);
let isLoading = false;
let selectedFile = null;

const messagesEl = document.getElementById('messages');
const inputEl    = document.getElementById('input');
const sendBtn    = document.getElementById('sendBtn');

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

function scrollBottom() {
  messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: 'smooth' });
}

function removeWelcome() {
  document.getElementById('welcome')?.remove();
}

// ── File select ──────────────────────────────────────────────
function onFileSelect(input) {
  if (!input.files[0]) return;
  selectedFile = input.files[0];
  document.getElementById('pdfName').textContent = selectedFile.name;
  document.getElementById('pdfPreview').classList.add('show');
}

function clearFile() {
  selectedFile = null;
  document.getElementById('fileInput').value = '';
  document.getElementById('pdfPreview').classList.remove('show');
}

// ── Render message with code detection ───────────────────────
function renderContent(text) {
  const parts = text.split(/(```[\s\S]*?```)/g);
  const frag  = document.createDocumentFragment();

  parts.forEach(part => {
    const codeMatch = part.match(/^```(\w*)\n?([\s\S]*?)```$/);
    if (codeMatch) {
      const lang = codeMatch[1] || 'text';
      const code = codeMatch[2].trim();
      const ext  = langToExt(lang);

      const block  = document.createElement('div');
      block.className = 'code-block';

      block.innerHTML = `
        <div class="code-header">
          <span>${lang}</span>
          <div class="code-actions">
            <button class="btn-code" onclick="copyCode(this)">คัดลอก</button>
            <button class="btn-code" onclick="downloadCode(this, '${lang}', '${ext}')">⬇ ดาวน์โหลด</button>
          </div>
        </div>
        <pre><code></code></pre>`;

      block.querySelector('code').textContent = code;
      frag.appendChild(block);
    } else if (part.trim()) {
      const p = document.createElement('span');
      p.style.whiteSpace = 'pre-wrap';
      p.textContent = part;
      frag.appendChild(p);
    }
  });
  return frag;
}

function langToExt(lang) {
  const map = {
    python:'py', javascript:'js', typescript:'ts', html:'html',
    css:'css', java:'java', cpp:'cpp', c:'c', go:'go',
    rust:'rs', bash:'sh', shell:'sh', sql:'sql', json:'json',
    yaml:'yaml', markdown:'md', php:'php', ruby:'rb', swift:'swift',
    kotlin:'kt', dart:'dart'
  };
  return map[lang.toLowerCase()] || 'txt';
}

function copyCode(btn) {
  const code = btn.closest('.code-block').querySelector('code').textContent;
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = '✓ คัดลอกแล้ว';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'คัดลอก'; btn.classList.remove('copied'); }, 2000);
  });
}

function downloadCode(btn, lang, ext) {
  const code     = btn.closest('.code-block').querySelector('code').textContent;
  const filename = `code_${Date.now()}.${ext}`;
  const blob     = new Blob([code], { type: 'text/plain' });
  const a        = document.createElement('a');
  a.href         = URL.createObjectURL(blob);
  a.download     = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Add message bubble ────────────────────────────────────────
function addMessage(role, text, pdfName) {
  removeWelcome();
  const wrap   = document.createElement('div');
  wrap.className = `msg ${role}`;

  const av = document.createElement('div');
  av.className = 'msg-av';
  av.textContent = role === 'bot' ? '🤖' : '👤';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  if (pdfName) {
    const badge = document.createElement('div');
    badge.className = 'pdf-badge';
    badge.innerHTML = `📄 <strong>${pdfName}</strong>`;
    bubble.appendChild(badge);
  }

  bubble.appendChild(renderContent(text));
  wrap.appendChild(av);
  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  scrollBottom();
}

function addTyping() {
  removeWelcome();
  const wrap = document.createElement('div');
  wrap.className = 'msg bot typing';
  wrap.id = 'typing';
  wrap.innerHTML = `<div class="msg-av">🤖</div>
    <div class="bubble"><div class="tdot"></div><div class="tdot"></div><div class="tdot"></div></div>`;
  messagesEl.appendChild(wrap);
  scrollBottom();
}

function removeTyping() { document.getElementById('typing')?.remove(); }

// ── Send ──────────────────────────────────────────────────────
async function sendMessage() {
  const text = inputEl.value.trim();
  if ((!text && !selectedFile) || isLoading) return;

  isLoading = true;
  sendBtn.disabled = true;
  inputEl.value = '';
  inputEl.style.height = 'auto';

  // ถ้ามีไฟล์ PDF
  if (selectedFile) {
    const file = selectedFile;
    clearFile();
    addMessage('user', text || `ช่วยสรุปไฟล์ ${file.name} ให้หน่อยครับ`);
    addTyping();

    const form = new FormData();
    form.append('file', file);
    form.append('session_id', SESSION_ID);

    try {
      const res  = await fetch(`/upload-pdf?session_id=${SESSION_ID}`, { method: 'POST', body: form });
      const data = await res.json();
      removeTyping();
      addMessage('bot', data.reply, data.filename);
    } catch {
      removeTyping();
      addMessage('bot', '❌ ไม่สามารถอัปโหลด PDF ได้ครับ');
    }

  } else {
    // ข้อความธรรมดา
    if (text.toLowerCase() in {'/reset':1,'ลืมทุกอย่าง':1,'เริ่มใหม่':1}) {
      addMessage('user', text);
    } else {
      addMessage('user', text);
    }
    addTyping();

    try {
      const res  = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: SESSION_ID, message: text }),
      });
      const data = await res.json();
      removeTyping();
      addMessage('bot', data.reply);
    } catch {
      removeTyping();
      addMessage('bot', '❌ ไม่สามารถเชื่อมต่อ server ได้ครับ');
    }
  }

  isLoading = false;
  sendBtn.disabled = false;
  inputEl.focus();
}

function useSug(btn) { inputEl.value = btn.textContent; sendMessage(); }
function resetChat()  { inputEl.value = '/reset'; sendMessage(); }

inputEl.focus();
</script>
</body>
</html>"""

# ── Main ──────────────────────────────────────────────────────────────────
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
║         Gemma Web Chat  v2               ║
╠══════════════════════════════════════════╣
║  Local:   http://localhost:{WEB_PORT}         ║
║  Network: http://{local_ip}:{WEB_PORT}    ║
╚══════════════════════════════════════════╝
  เปิด URL บนมือถือเพื่อใช้งาน (WiFi เดียวกัน)
    """)
    uvicorn.run("webchat:app", host="0.0.0.0", port=WEB_PORT, reload=False)