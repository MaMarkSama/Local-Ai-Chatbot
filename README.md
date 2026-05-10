# 🤖 Local-Ai-Chatbot

Chatbot ขับเคลื่อนด้วย Local AI (Gemma 9B) ผ่าน Ollama  
รองรับทั้ง **Line Bot** และ **Web Chat** แบบ Claude.ai

---

## ✨ Features

### 💬 Line Bot
- ตอบข้อความสนทนาทั่วไป
- อ่านและสรุปไฟล์ PDF อัตโนมัติ
- จำประวัติการสนทนาต่อ user

### 🌐 Web Chat
- UI คล้าย Claude.ai มี Sidebar สลับแชทได้
- Dark / Light Mode
- ค้นหาแชท, เปลี่ยนชื่อ, ลบแชท
- ประวัติการสนทนาเก็บใน SQLite ไม่หายแม้ restart

### 📁 ไฟล์ที่รองรับ
| ประเภท | นามสกุล | ความสามารถ |
|---|---|---|
| PDF | .pdf | อ่านแบบ chunk + สรุปอัจฉริยะ |
| CSV | .csv | วิเคราะห์โครงสร้างและข้อมูล |
| XML | .xml | แปลงเป็นข้อความอ่านง่าย |
| Word | .docx, .doc | อ่านข้อความและตาราง |
| Excel | .xlsx, .xls | อ่านทุก Sheet |
| Text | .txt, .md, .json, .yaml ฯลฯ | อ่านตรงๆ |
| รูปภาพ | .jpg, .png, .gif, .webp | วิเคราะห์ด้วย Vision AI |

### 🔧 อื่นๆ
- Code Block พร้อมปุ่มคัดลอก + ดาวน์โหลดเป็นไฟล์
- แยก session Line Bot vs Web Chat ไม่สับสน
- รันบน WiFi/Hotspot เดียวกัน ไม่ต้องใช้เน็ตออกนอก (Web Chat)

---

## 📋 สิ่งที่ต้องมี

- Python 3.10+
- [Ollama](https://ollama.com) ติดตั้งและรัน
- Line Developer Account (สำหรับ Line Bot)
- [ngrok](https://ngrok.com) (สำหรับ Line Bot)

---

## 🚀 ติดตั้ง

```bash
# 1. Pull Model
ollama pull gemma3:9b

# 2. ติดตั้ง dependencies
pip install -r requirements.txt

# 3. ตั้งค่า .env
cp .env.example .env
# แก้ไข LINE_CHANNEL_ACCESS_TOKEN และ LINE_CHANNEL_SECRET
```

---

## 🗂️ โครงสร้างโปรเจกต์

```
Local-Ai-Chatbot/
├── main.py            # Line Bot (port 8000)
├── webchat.py         # Web Chat (port 8001)
├── file_processor.py  # อ่านและแปลงไฟล์ทุกประเภท
├── conversation.py    # จัดการ chat history (RAM + SQLite)
├── database.py        # SQLite manager
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 🖥️ รัน

```bash
# Terminal 1 — Line Bot
python main.py

# Terminal 2 — Web Chat
python webchat.py

# Terminal 3 — ngrok (Line Bot เท่านั้น)
ngrok.exe http --domain=your-domain.ngrok-free.app 8000
```

**Web Chat เปิดบนมือถือ (WiFi/Hotspot เดียวกัน):**
```
http://192.168.x.x:8001
```

---

## ⚙️ ตัวแปรใน .env

| Key | Default | หมายเหตุ |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | — | จาก Line Console |
| `LINE_CHANNEL_SECRET` | — | จาก Line Console |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | URL ของ Ollama |
| `OLLAMA_MODEL` | `gemma3:9b` | Text model |
| `OLLAMA_VISION_MODEL` | (ค่าเดียวกับ OLLAMA_MODEL) | Vision model สำหรับวิเคราะห์รูป |
| `SYSTEM_PROMPT` | ดู .env.example | ปรับบุคลิก AI |
| `PDF_MAX_CHARS` | `20000` | ขนาดสูงสุดของ PDF ต่อ chunk |
| `WEB_PORT` | `8001` | Port ของ Web Chat |

---

## ✅ Health Check

```bash
curl http://localhost:8000/health   # Line Bot
curl http://localhost:8001/health   # Web Chat
```

---

## 📸 ตัวอย่างการใช้งาน

<!-- ![Web Chat Demo](assets/webchat-demo.png) -->
<!-- ![Line Bot Demo](assets/line-demo.png) -->

---

## 🛠️ Tech Stack

- **FastAPI** — Web Framework
- **Ollama** — Local AI Runtime
- **Gemma 9B** — AI Model (Text + Vision)
- **SQLite** — เก็บประวัติการสนทนา
- **line-bot-sdk** — Line Messaging API
- **pypdf** — PDF Reader
- **python-docx** — Word Document Reader
- **openpyxl** — Excel Reader
- **ngrok** — Public Tunnel (Line Bot)