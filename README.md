# 🤖 Local-Ai-Chatbot

Chatbot ขับเคลื่อนด้วย Local AI (Gemma / Llama / Qwen) ผ่าน Ollama
รองรับทั้ง **Line Bot**, **Web Chat** พร้อมระบบ **User** และ **Memory**

---

## ✨ Features

### 🌐 Web Chat
- **Web Search** — ค้นหาข้อมูลจากอินเทอร์เน็ตมาตอบได้ (DuckDuckGo)
- UI คล้าย Claude.ai — Sidebar สลับแชทได้
- **Login / Register / Guest mode**
- **Chat History** แยกต่อ user — User Isolation สมบูรณ์
- Dark / Light Mode
- ค้นหาแชท, เปลี่ยนชื่อ, ลบแชท

### 🧠 Memory System
- **Rolling Summary** — สรุปบทสนทนาเก่าอัตโนมัติเมื่อยาวเกิน
- **Long-term Memory** — จำข้อมูลสำคัญของผู้ใช้ข้ามวัน
- **Context Injection** — ใส่ memory เข้า prompt อัตโนมัติ
- ดูและลบ memory ได้จาก UI

### 💬 Line Bot
- ตอบข้อความสนทนาทั่วไป
- อ่านและสรุปไฟล์ PDF อัตโนมัติ
- จำประวัติการสนทนาต่อ user

### 📁 ไฟล์ที่รองรับ
| ประเภท | นามสกุล | ความสามารถ |
|---|---|---|
| PDF | .pdf | อ่านแบบ chunk + สรุปอัจฉริยะ |
| CSV | .csv | วิเคราะห์โครงสร้างและข้อมูล |
| XML | .xml | แปลงเป็นข้อความอ่านง่าย |
| Word | .docx, .doc | อ่านข้อความและตาราง |
| Excel | .xlsx, .xls | อ่านทุก Sheet |
| Text | .txt, .md, .json ฯลฯ | อ่านตรงๆ |
| รูปภาพ | .jpg, .png, .gif, .webp | วิเคราะห์ด้วย Vision AI |
| Web | — | ค้นหาข้อมูลล่าสุดจากอินเทอร์เน็ต |

### 🔧 อื่นๆ
- Code Block พร้อมปุ่มคัดลอก + ดาวน์โหลดเป็นไฟล์
- GUI Launcher เปิด/ปิดแต่ละ service
- Setup Wizard ติดตั้งครั้งแรกผ่าน GUI
- Build เป็น .exe ได้ด้วย PyInstaller

---

## 📋 สิ่งที่ต้องมี

- Python 3.10+
- [Ollama](https://ollama.com) ติดตั้งและรัน
- Line Developer Account (สำหรับ Line Bot)
- [ngrok](https://ngrok.com) (สำหรับ Line Bot)

---

## 🚀 ติดตั้ง

### วิธีที่ 1 — Setup Wizard (แนะนำ)
```
ดับเบิ้ลคลิ๊ก install.bat
```
Wizard จะนำทางทุกขั้นตอนให้

### วิธีที่ 2 — Manual
```bash
# Pull Model
ollama pull gemma3:9b

# ติดตั้ง dependencies
pip install -r requirements.txt

# ตั้งค่า .env
cp .env.example .env
```

---

## 🗂️ โครงสร้างโปรเจกต์

```
Local-Ai-Chatbot/
├── main.py              # Line Bot (port 8000)
├── webchat.py           # Web Chat API (port 8001)
├── webchat_ui.html      # Web Chat UI (Login + Chat)
├── file_processor.py    # อ่านไฟล์ทุกประเภท
├── conversation.py      # จัดการ chat history
├── database.py          # SQLite manager
├── memory_manager.py    # Memory + Summary system
├── user_manager.py      # Login/Register/Session
├── auth_middleware.py   # FastAPI auth helpers
├── launcher.py          # GUI Launcher
├── run.py               # CLI launcher (รวม server)
├── setup_wizard.py      # GUI Setup Wizard
├── requirements.txt
├── .env.example
├── start.bat            # เปิด Launcher
├── install.bat          # ติดตั้งครั้งแรก
└── build_system/
    ├── build.bat        # Build .exe installer
    ├── build.py         # Build script
    └── README_BUILD.md
```

---

## 🖥️ รัน

```bash
# Terminal 1 — Line Bot
python main.py

# Terminal 2 — Web Chat
python webchat.py

# Terminal 3 — ngrok
ngrok.exe http --domain=your-domain.ngrok-free.app 8000
```

หรือดับเบิ้ลคลิ๊ก `start.bat` เพื่อเปิด GUI Launcher

---

## ⚙️ ตัวแปรใน .env

| Key | Default | หมายเหตุ |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | — | จาก Line Console |
| `LINE_CHANNEL_SECRET` | — | จาก Line Console |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | URL ของ Ollama |
| `OLLAMA_MODEL` | `gemma3:9b` | Text model |
| `OLLAMA_VISION_MODEL` | (เดียวกับ MODEL) | Vision model |
| `SYSTEM_PROMPT` | ดู .env.example | ปรับบุคลิก AI |
| `PDF_MAX_CHARS` | `20000` | ขนาดสูงสุด PDF |
| `WEB_PORT` | `8001` | Port Web Chat |
| `JWT_SECRET` | (สุ่มอัตโนมัติ) | Secret สำหรับ token |
| `TOKEN_EXPIRE_DAYS` | `30` | อายุ session |
| `NGROK_DOMAIN` | — | Static domain ของ ngrok |

---

## 🔐 User System

| Feature | รายละเอียด |
|---|---|
| Register | สมัครด้วย username + password |
| Login | Cookie-based session (30 วัน) |
| Guest | ใช้งานได้โดยไม่ต้อง login |
| User Isolation | แต่ละ user เห็นแชทของตัวเองเท่านั้น |
| Change Password | เปลี่ยนรหัสผ่านได้จาก UI |

---

## 🧠 Memory System

| Feature | รายละเอียด |
|---|---|
| Rolling Summary | สรุปบทสนทนาเก่าอัตโนมัติเมื่อ > 20 คู่ |
| Long-term Facts | จำข้อมูลสำคัญของ user ข้ามวัน |
| Context Injection | ใส่ memory เข้า system prompt อัตโนมัติ |
| Memory View | ดู memory ได้จากปุ่ม 🧠 ใน UI |
| Clear Memory | ลบ memory ได้จาก user menu |

---

## 🔗 API Endpoints

### Auth
| Method | Path | หมายเหตุ |
|---|---|---|
| POST | `/api/auth/register` | สมัครสมาชิก |
| POST | `/api/auth/login` | เข้าสู่ระบบ |
| POST | `/api/auth/logout` | ออกจากระบบ |
| GET  | `/api/auth/me` | ข้อมูล user ปัจจุบัน |

### Chat
| Method | Path | หมายเหตุ |
|---|---|---|
| POST | `/chat` | ส่งข้อความ |
| POST | `/upload-file` | อัปโหลดไฟล์ |

### Sessions
| Method | Path | หมายเหตุ |
|---|---|---|
| GET    | `/api/sessions` | รายการแชท |
| GET    | `/api/sessions/{id}` | ข้อความในแชท |
| POST   | `/api/sessions/{id}/rename` | เปลี่ยนชื่อ |
| DELETE | `/api/sessions/{id}` | ลบแชท |

### Memory
| Method | Path | หมายเหตุ |
|---|---|---|
| GET    | `/api/memory` | ดู memory |
| DELETE | `/api/memory` | ลบ memory ทั้งหมด |

---

## 🛠️ Tech Stack

- **FastAPI** — Web Framework
- **Ollama** — Local AI Runtime
- **Gemma / Llama / Qwen** — AI Models
- **SQLite** — Database (chat, users, memory)
- **line-bot-sdk** — Line Messaging API
- **pypdf / python-docx / openpyxl** — File processors
- **ngrok** — Public Tunnel (Line Bot)
- **PyInstaller** — Build .exe installer
- **tkinter** — GUI (Launcher + Setup Wizard)
