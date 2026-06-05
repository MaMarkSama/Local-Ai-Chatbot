import os
import io
import httpx
import logging
import asyncio
import pypdf
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FileMessageContent, ImageMessageContent
from dotenv import load_dotenv
from conversation import ConversationManager
from memory_manager import MemoryManager
from web_searcher import web_searcher
from usage_tracker import usage_tracker

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET       = os.getenv("LINE_CHANNEL_SECRET")
OLLAMA_BASE_URL           = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL              = os.getenv("OLLAMA_MODEL", "gemma3:9b")
OLLAMA_VISION_MODEL       = os.getenv("OLLAMA_VISION_MODEL", OLLAMA_MODEL)
OLLAMA_NUM_CTX            = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
SYSTEM_PROMPT             = os.getenv("SYSTEM_PROMPT", (
    "คุณคือผู้ช่วย AI ที่ฉลาดและเป็นมิตร ตอบคำถามภาษาไทยได้อย่างชัดเจน "
    "กระชับ และเป็นประโยชน์ หากต้องเน้นข้อความให้ใช้ Markdown แบบพอดี "
    "หากไม่แน่ใจให้บอกตรง ๆ"
))
PDF_MAX_CHARS = int(os.getenv("PDF_MAX_CHARS", "6000"))

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise ValueError("กรุณาตั้งค่า LINE_CHANNEL_ACCESS_TOKEN และ LINE_CHANNEL_SECRET ใน .env")

# ── Line SDK ─────────────────────────────────────────────────────────────
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler       = WebhookHandler(LINE_CHANNEL_SECRET)

# ── FastAPI App ──────────────────────────────────────────────────────────
app = FastAPI(title="Line Bot + Gemma via Ollama", version="2.0.0")

# ── Conversation Manager ─────────────────────────────────────────────────
conversation_manager = ConversationManager(max_turns=10)
memory_manager = MemoryManager(OLLAMA_BASE_URL, OLLAMA_MODEL, SYSTEM_PROMPT)


# ── Download File จาก Line ────────────────────────────────────────────────
async def download_line_file(message_id: str) -> bytes:
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.content


# ── Extract ข้อความจาก PDF ───────────────────────────────────────────────
def extract_pdf_text(pdf_bytes: bytes, max_chars: int = 6000) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages  = []
        total  = 0
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            pages.append(f"[หน้า {i+1}]\n{text}")
            total += len(text)
            if total >= max_chars:
                break
        full_text = "\n\n".join(pages)
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars] + "\n...(ข้อความถูกตัดเนื่องจากยาวเกินไป)"
        return full_text if full_text else "(ไม่พบข้อความในไฟล์ PDF นี้)"
    except Exception as e:
        logger.error(f"PDF extract error: {e}")
        return f"(อ่าน PDF ไม่ได้: {e})"


# ── Ollama Chat ───────────────────────────────────────────────────────────
async def chat_with_ollama(user_id: str, user_message: str, display_message: str = None) -> str:
    # เธชเธฃเธธเธ memory เธ–เนเธฒเธเธ—เธชเธเธ—เธเธฒเธขเธฒเธงเน€เธเธดเธ
    messages = conversation_manager.get_messages(user_id)
    messages = await memory_manager.maybe_summarize(user_id, user_id, messages)

    msg_to_save = display_message if display_message is not None else user_message
    conversation_manager.add_message(user_id, "user", msg_to_save, "line", user_id=user_id)
    messages = conversation_manager.get_messages(user_id)

    # System prompt + memory context
    enhanced_prompt = memory_manager.get_enhanced_system_prompt(user_id, user_id)

    payload_messages = [{"role": "system", "content": enhanced_prompt}] + messages[:-1] + [{"role": "user", "content": user_message}]

    payload = {
        "model":    OLLAMA_MODEL,
        "messages": payload_messages,
        "stream":   False,
        "options": {
            "temperature":    0.7,
            "num_predict":    -1,
            "num_ctx":        OLLAMA_NUM_CTX,
            "repeat_penalty": 1.1,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            reply_content = data["message"]["content"]
            
            # Capture tokens
            p_tokens = data.get("prompt_eval_count", 0)
            c_tokens = data.get("eval_count", 0)

        conversation_manager.add_message(user_id, "assistant", reply_content, "line", user_id=user_id)
        # Record usage (user_id is session_id for line bot)
        usage_tracker.record_usage(user_id, p_tokens, c_tokens)
        reply = reply_content.strip()
        return reply if reply else "⚠️ ขอโทษครับ ไม่สามารถสร้างคำตอบได้ กรุณาลองใหม่"

    except httpx.ConnectError:
        return "⚠️ ไม่สามารถเชื่อมต่อกับ Ollama ได้ กรุณาตรวจสอบว่า Ollama กำลังทำงานอยู่"
    except httpx.TimeoutException:
        return "⏳ Ollama ใช้เวลานานเกินไป กรุณาลองใหม่อีกครั้ง"
    except Exception as e:
        logger.error(f"Ollama error: {e}")
        return f"❌ เกิดข้อผิดพลาด: {str(e)}"


# ── Webhook Endpoint ──────────────────────────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body      = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return JSONResponse(content={"status": "ok"})


# ── Handler: ข้อความ ─────────────────────────────────────────────────────
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event: MessageEvent):
    asyncio.create_task(_handle_text_async(event))


async def _handle_text_async(event: MessageEvent):
    user_id      = event.source.user_id
    user_message = event.message.text
    logger.info(f"[{user_id}] TEXT → {user_message}")

    if user_message.strip().lower() in ["/reset", "ลืมทุกอย่าง", "เริ่มใหม่"]:
        conversation_manager.clear(user_id)
        reply = "🔄 ล้างประวัติการสนทนาแล้ว เริ่มต้นใหม่ได้เลยครับ!"
    elif user_message.strip().lower().startswith("/search "):
        query = user_message[8:].strip()
        search_results = web_searcher.search(query)
        if search_results:
            enhanced_message = f"""
[ข้อมูลล่าสุดจากอินเทอร์เน็ต]
{search_results}

[คำแนะนำ]
กรุณาตอบคำถามของผู้ใช้โดยใช้ข้อมูลจาก Web Search ด้านบนเป็นหลัก ตอบเป็นภาษาไทยที่กระชับและเข้าใจง่าย

[คำถามของผู้ใช้]
{query}
"""
            reply = await chat_with_ollama(user_id, enhanced_message, display_message=query)
        else:
            reply = "ไม่พบข้อมูลจากการค้นหาเว็บครับ"
    else:
        reply = await chat_with_ollama(user_id, user_message)

    logger.info(f"[{user_id}] ← {reply[:80]}...")
    async with AsyncApiClient(configuration) as api_client:
        await AsyncMessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)],
            )
        )


# ── Handler: ไฟล์ PDF ────────────────────────────────────────────────────
@handler.add(MessageEvent, message=FileMessageContent)
def handle_file(event: MessageEvent):
    asyncio.create_task(_handle_file_async(event))


async def _handle_file_async(event: MessageEvent):
    user_id   = event.source.user_id
    file_name = event.message.file_name
    logger.info(f"[{user_id}] FILE → {file_name}")

    # ตอบรับทันทีว่ากำลังอ่าน
    async with AsyncApiClient(configuration) as api_client:
        await AsyncMessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=f"📄 กำลังอ่านไฟล์ {file_name} อยู่นะครับ รอสักครู่...")],
            )
        )

    # ตรวจสอบว่าเป็น PDF
    if not file_name.lower().endswith(".pdf"):
        async with AsyncApiClient(configuration) as api_client:
            await AsyncMessagingApi(api_client).push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text=f"⚠️ รองรับเฉพาะไฟล์ PDF ครับ ไฟล์ {file_name} ยังไม่รองรับ")],
                )
            )
        return

    try:
        pdf_bytes = await download_line_file(event.message.id)
        pdf_text  = extract_pdf_text(pdf_bytes, max_chars=PDF_MAX_CHARS)
        logger.info(f"[{user_id}] PDF extracted: {len(pdf_text)} chars")

        prompt = f"""คุณเป็นผู้เชี่ยวชาญด้านการวิเคราะห์เอกสาร
ไฟล์ PDF ชื่อ '{file_name}' มีเนื้อหาดังนี้:

[เนื้อหาเอกสาร]
{pdf_text}

[คำสั่ง]
กรุณาสรุปเนื้อหาสำคัญของเอกสารนี้ให้กระชับและเข้าใจง่าย สกัดข้อมูลสำคัญเป็น Bullet points"""
        reply = await chat_with_ollama(user_id, prompt, display_message=f"[ส่งไฟล์ PDF: {file_name}] ขอสรุปเอกสาร")

    except Exception as e:
        logger.error(f"[{user_id}] PDF error: {e}")
        reply = f"❌ ไม่สามารถอ่านไฟล์ PDF ได้ครับ: {str(e)}"

    async with AsyncApiClient(configuration) as api_client:
        await AsyncMessagingApi(api_client).push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=f"📋 สรุปไฟล์ {file_name}:\n\n{reply}")],
            )
        )



# ── Handler: รูปภาพ (Multimodal) ──────────────────────────────────────────────────────────
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event: MessageEvent):
    asyncio.create_task(_handle_image_async(event))

async def _handle_image_async(event: MessageEvent):
    user_id = event.source.user_id
    logger.info(f"[{user_id}] IMAGE received")
    
    async with AsyncApiClient(configuration) as api_client:
        await AsyncMessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="🖼️ กำลังวิเคราะห์รูปภาพ...")]
            )
        )
    
    try:
        image_bytes = await download_line_file(event.message.id)
        import base64
        b64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        payload = {
            "model": OLLAMA_VISION_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "วิเคราะห์และอธิบายรูปภาพนี้อย่างละเอียด", "images": [b64_image]}
            ],
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": -1, "num_ctx": OLLAMA_NUM_CTX}
        }
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            reply = data["message"]["content"].strip()
            p_tokens = data.get("prompt_eval_count", 0)
            c_tokens = data.get("eval_count", 0)
            
        conversation_manager.add_message(user_id, "user", "[ส่งรูปภาพ]", "line", user_id=user_id)
        conversation_manager.add_message(user_id, "assistant", reply, "line", user_id=user_id)
        try:
            from usage_tracker import usage_tracker
            usage_tracker.record_usage(user_id, p_tokens, c_tokens)
        except: pass
        
    except Exception as e:
        logger.error(f"[{user_id}] Image processing error: {e}")
        reply = f"❌ ไม่สามารถวิเคราะห์รูปภาพได้: {e}"

    async with AsyncApiClient(configuration) as api_client:
        await AsyncMessagingApi(api_client).push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=reply)]
            )
        )

# ── Health Check ──────────────────────────────────────────────────────────
@app.get("/health")
async def health_check():
    ollama_status = "unknown"
    model_loaded  = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if r.status_code == 200:
                models        = [m["name"] for m in r.json().get("models", [])]
                ollama_status = "online"
                model_loaded  = any(OLLAMA_MODEL in m for m in models)
    except Exception:
        ollama_status = "offline"

    return {
        "status":       "ok",
        "ollama":       ollama_status,
        "model":        OLLAMA_MODEL,
        "model_loaded": model_loaded,
        "features":     ["text", "pdf"],
    }


# ── Main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
