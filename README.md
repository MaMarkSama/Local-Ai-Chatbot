# 🤖 Line-Gemma-Chatbot

Chatbot ขับเคลื่อนด้วย Local AI (Gemma 4 9B) ผ่าน Ollama รองรับทั้ง **Line Bot** และ **Web Chat**

---

## ✨ Features

- 💬 **Line Bot** — ตอบข้อความและสรุปไฟล์ PDF อัตโนมัติ
- 🌐 **Web Chat** — เปิดใช้งานบนมือถือผ่าน WiFi/Hotspot เดียวกัน ไม่ต้องใช้เน็ตออกนอก
- 🧠 **จำประวัติการสนทนา** — จำย้อนหลัง 10-20 คู่ต่อ user
- 📄 **อ่านสรุป PDF** — รองรับทั้ง Line Bot และ Web Chat
- 💻 **Code Block** — แสดงโค้ดสวยงาม พร้อมปุ่มคัดลอกและดาวน์โหลดเป็นไฟล์
- 🔄 **Reset** — พิมพ์ `/reset` หรือ `เริ่มใหม่` เพื่อล้างประวัติ

---

## 📋 สิ่งที่ต้องมี

- Python 3.10+
- [Ollama](https://ollama.com) ติดตั้งและรันอยู่
- Line Developer Account (สำหรับ Line Bot)
- [ngrok](https://ngrok.com) (สำหรับ Line Bot)

---

## 🚀 ขั้นตอนติดตั้ง

### 1. Pull Model และติดตั้ง Dependencies

```bash
# Pull Gemma 9B
ollama pull gemma4:e4b

# ติดตั้ง Python packages
pip install -r requirements.txt
```

### 2. ตั้งค่า Environment Variables

```bash
cp .env.example .env
# แก้ไขค่าใน .env
```

---

## 🗂️ โครงสร้างโปรเจกต์

```
Local-Ai-Chatbot/
├── main.py            # Line Bot (port 8000)
├── webchat.py         # Web Chat (port 8001)
├── conversation.py    # จัดการ chat history
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 💬 Line Bot

### รัน Server

```bash
python main.py
```

### เปิด Public URL

```bash
# Static domain (แนะนำ URL ไม่เปลี่ยน)
ngrok.exe http --domain=your-domain.ngrok-free.app 8000

# หรือแบบชั่วคราว
ngrok.exe http 8000
```

### ตั้งค่า Webhook ใน Line Console

```
https://your-domain.ngrok-free.app/webhook
```

👉 https://developers.line.biz/console/

### คำสั่งพิเศษ

| พิมพ์ | ผล |
|---|---|
| `/reset` | ล้างประวัติสนทนา |
| `ลืมทุกอย่าง` | ล้างประวัติสนทนา |
| `เริ่มใหม่` | ล้างประวัติสนทนา |
| ส่งไฟล์ .pdf | AI อ่านและสรุปให้อัตโนมัติ |

---

## 🌐 Web Chat (ไม่ต้องใช้เน็ตออกนอก)

### รัน Server

```bash
python webchat.py
```

จะแสดง IP ของเครื่องอัตโนมัติ:

```
╔══════════════════════════════════════════╗
║         Gemma Web Chat  v2               ║
╠══════════════════════════════════════════╣
║  Local:   http://localhost:8001          ║
║  Network: http://192.168.x.x:8001       ║
╚══════════════════════════════════════════╝
```

### เปิดบนมือถือ

1. เชื่อม WiFi หรือ Hotspot เดียวกับคอม
2. เปิด Browser พิมพ์ `http://192.168.x.x:8001`
3. ใช้งานได้เลย ไม่ต้องติดตั้งอะไรเพิ่ม ✅

### Features ใน Web Chat

| Feature | วิธีใช้ |
|---|---|
| 📄 อัปโหลด PDF | กดปุ่ม "อัปโหลด PDF" → เลือกไฟล์ → กด Send |
| 💻 ดาวน์โหลดโค้ด | ถามโค้ด → กดปุ่ม ⬇ ดาวน์โหลด ได้ไฟล์ทันที |
| 📋 คัดลอกโค้ด | กดปุ่ม "คัดลอก" ในกล่องโค้ด |
| 🔄 เริ่มใหม่ | กดปุ่ม "เริ่มใหม่" มุมบนขวา |

---

## ⚙️ ตัวแปรใน .env

| Key | ค่า Default | หมายเหตุ |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | — | จาก Line Console |
| `LINE_CHANNEL_SECRET` | — | จาก Line Console |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | URL ของ Ollama |
| `OLLAMA_MODEL` | `gemma3:9b` | ชื่อ model จาก `ollama list` |
| `SYSTEM_PROMPT` | ดู .env.example | ปรับบุคลิก AI |
| `PDF_MAX_CHARS` | `6000` | ขนาดสูงสุดของ PDF ที่อ่าน |
| `WEB_PORT` | `8001` | Port ของ Web Chat |

---

## 🖥️ รันพร้อมกันทั้งสองโหมด

```bash
# Terminal 1 — Line Bot
python main.py

# Terminal 2 — Web Chat
python webchat.py

# Terminal 3 — ngrok (สำหรับ Line Bot)
ngrok.exe http --domain=your-domain.ngrok-free.app 8000
```

---

## ✅ Health Check

```bash
# Line Bot
curl http://localhost:8000/health

# Web Chat
curl http://localhost:8001/health
```

---

## 📸 ตัวอย่างการใช้งาน

<!-- วางรูปภาพตัวอย่างที่นี่ -->
<!-- ![Line Bot Demo](assets/line-demo.png) -->
<!-- ![Web Chat Demo](assets/webchat-demo.png) -->

---

## 🛠️ Tech Stack

- **FastAPI** — Web Framework
- **Ollama** — Local AI Runtime
- **Gemma 4 e4b** — AI Model
- **line-bot-sdk** — Line Messaging API
- **pypdf** — PDF Reader
- **python-multipart** — File Upload
- **ngrok** — Public Tunnel (Line Bot)