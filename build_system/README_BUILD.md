# 🔨 Build System — GemmaAI Installer

## โครงสร้างโฟลเดอร์

```
Local-Ai-Chatbot/
├── build_system/           ← โฟลเดอร์นี้
│   ├── build.bat           ← ดับเบิ้ลคลิ๊กเพื่อ build
│   ├── build.py            ← build script หลัก
│   └── README_BUILD.md     ← ไฟล์นี้
│
├── main.py                 ← ไฟล์โปรแกรม
├── webchat.py
├── setup_wizard.py
└── ...
```

---

## วิธี Build

```
1. วางโฟลเดอร์ build_system/ ใน Local-Ai-Chatbot/
2. ดับเบิ้ลคลิ๊ก build_system/build.bat
3. รอประมาณ 2-5 นาที
4. ได้ไฟล์ที่ build_system/dist/GemmaAI_Setup.exe
```

---

## ปรับแต่ง BUILD_CONFIG ใน build.py

```python
BUILD_CONFIG = {
    # ชื่อไฟล์ .exe ที่ได้
    "app_name":    "GemmaAI_Setup",

    # เวอร์ชัน (แสดงใน Properties ของ .exe)
    "app_version": "1.0.0",

    # ชื่อผู้พัฒนา
    "app_author":  "MaMarkSama",

    # ไฟล์ที่จะรวมใน installer
    "bundle_files": [
        "main.py",
        "webchat.py",
        ...
    ],

    # ไอคอน .exe (ใส่ path ไปยัง .ico หรือ None)
    "icon": None,
    # ตัวอย่าง: "icon": "assets/icon.ico"
}
```

---

## เพิ่มไฟล์ใหม่เข้า installer

แก้ `bundle_files` ใน `BUILD_CONFIG`:

```python
"bundle_files": [
    "main.py",
    "webchat.py",
    "my_new_file.py",  # ← เพิ่มตรงนี้
    ...
],
```

---

## เปลี่ยนเวอร์ชัน

แก้แค่บรรทัดเดียว:
```python
"app_version": "1.1.0",
```

---

## Output

```
build_system/dist/
├── GemmaAI_Setup.exe           ← ไฟล์ติดตั้ง (แจกให้ลูกค้า)
└── GemmaAI_Setup_v1.0.0.zip   ← zip พร้อม README
```

---

## การทำงานของ .exe

เมื่อลูกค้าดับเบิ้ลคลิ๊ก `GemmaAI_Setup.exe`:

```
1. Extract ไฟล์โปรแกรมไปที่ AppData/Local/GemmaAI/_src/
2. เปิด GUI Setup Wizard
3. ผู้ใช้เลือก install path, model, ตั้งค่า Line Bot
4. Copy ไฟล์ไปยัง install path ที่เลือก
5. เสร็จ → เปิด Launcher ได้เลย
```

---

## Requirements

- Python 3.10+
- PyInstaller (ติดตั้งอัตโนมัติเมื่อรัน build.bat)
