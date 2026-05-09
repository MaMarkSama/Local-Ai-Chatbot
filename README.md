# 🤖 Line Bot + Gemma 4 (9B) via Ollama

Chatbot บน Line ที่ขับเคลื่อนด้วย AI แบบ Local ผ่าน Ollama

---

## 📋 สิ่งที่ต้องมี

- Python 3.10+
- [Ollama](https://ollama.com) ติดตั้งและรัน
- Line Developer Account

---

## 🚀 ขั้นตอนติดตั้ง

### 1. ติดตั้ง Ollama และ Pull Model

```bash
# ติดตั้ง Ollama (macOS/Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Pull Gemma 9B (ใช้ชื่อที่ Ollama รองรับ)
ollama pull gemma4:e4b

# ตรวจสอบ model ที่มี
ollama list
```

### 2. Clone / วางโปรเจกต์แล้วติดตั้ง dependencies

```bash
cd line-gemma-bot
pip install -r requirements.txt
```

### 3. ตั้งค่า Environment Variables

```bash
cp .env.example .env
# แก้ไขค่าใน .env ด้วย editor ที่ชอบ
nano .env
```

ค่าที่ต้องใส่:
| Key | หาได้จาก |
|-----|----------|
| `LINE_CHANNEL_ACCESS_TOKEN` | Line Console → Messaging API → Channel access token |
| `LINE_CHANNEL_SECRET` | Line Console → Basic settings → Channel secret |
| `OLLAMA_MODEL` | ชื่อ model จาก `ollama list` |

### 4. รัน Server

```bash
python main.py
# หรือ
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. เปิด Public URL ด้วย ngrok (สำหรับ development)

```bash
# ติดตั้ง ngrok: https://ngrok.com
ngrok http 8000
```

Copy URL เช่น `https://xxxx.ngrok-free.app` แล้วตั้งเป็น Webhook URL ใน Line Console:
```
https://xxxx.ngrok-free.app/webhook
```

---

## ✅ ตรวจสอบสถานะ

```bash
# Health check
curl http://localhost:8000/health

# ผลลัพธ์ที่ควรได้
{
  "status": "ok",
  "ollama": "online",
  "model": "gemma3:9b",
  "model_loaded": true
}
```

---

## 💬 คำสั่งพิเศษใน Line Chat

| พิมพ์ | ผลลัพธ์ |
|-------|---------|
| `/reset` | ล้างประวัติการสนทนา |
| `ลืมทุกอย่าง` | ล้างประวัติการสนทนา |
| `เริ่มใหม่` | ล้างประวัติการสนทนา |

---

## 🏗️ โครงสร้างโปรเจกต์

```
line-gemma-bot/
├── main.py            # FastAPI app หลัก + Line Webhook
├── conversation.py    # จัดการ chat history ต่อ user
├── requirements.txt
├── .env.example       # ตัวอย่างค่า config
└── README.md
```

---

## ⚙️ ปรับแต่ง

**เปลี่ยน System Prompt** — แก้ค่า `SYSTEM_PROMPT` ใน `.env`

**ปรับ max history** — แก้ `max_turns` ใน `main.py`:
```python
conversation_manager = ConversationManager(max_turns=20)
```

**ปรับ temperature / token** — แก้ `options` ใน `chat_with_ollama()`:
```python
"options": {
    "temperature": 0.5,   # ต่ำ = ตอบแน่วแน่กว่า, สูง = สร้างสรรค์กว่า
    "num_predict": 1024,  # จำนวน token สูงสุดต่อการตอบ
}
```
