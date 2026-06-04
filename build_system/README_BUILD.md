# 🔨 Build System — GemmaAI Installer

## โครงสร้างโฟลเดอร์ที่ถูกต้อง

```
final_package/               ← โฟลเดอร์หลักของโปรเจกต์
├── build_system/            ← โฟลเดอร์นี้
│   ├── build.bat            ← ดับเบิ้ลคลิกเพื่อ build
│   ├── build.py             ← build script หลัก
│   └── README_BUILD.md      ← ไฟล์นี้
│
├── main.py
├── webchat.py
├── setup_wizard.py          ← ต้องอยู่ตรงนี้ (ใน parent ของ build_system/)
├── install.bat
└── ...ไฟล์โปรแกรมอื่น ๆ
```

> ⚠️ **สำคัญ:** `build_system/` ต้องอยู่ **ภายใน** โฟลเดอร์โปรแกรม  
> ไม่ใช่ข้าง ๆ กัน

---

## วิธี Build

```
1. วาง build_system/ ไว้ใน final_package/ (ดูโครงสร้างด้านบน)
2. ดับเบิ้ลคลิก build_system/build.bat
3. รอประมาณ 2–5 นาที
4. ได้ไฟล์ที่ build_system/dist/GemmaAI_Setup.exe
```

---

## ปรับแต่ง BUILD_CONFIG ใน build.py

```python
BUILD_CONFIG = {
    # ชื่อไฟล์ .exe ที่ได้
    "app_name":    "GemmaAI_Setup",

    # เวอร์ชัน (แสดงใน Properties ของ .exe)
    "app_version": "1.0.1",

    # ชื่อผู้พัฒนา
    "app_author":  "MaMarkSama",

    # ไฟล์ที่จะรวมใน installer
    "bundle_files": [
        "main.py",
        "webchat.py",
        # เพิ่มไฟล์ใหม่ตรงนี้
    ],

    # ไอคอน .exe (ใส่ path ไปยัง .ico หรือ None)
    "icon": None,
    # ตัวอย่าง: "icon": "../assets/icon.ico"
}
```

---

## เพิ่มไฟล์ใหม่เข้า installer

แก้ `bundle_files` ใน `BUILD_CONFIG`:

```python
"bundle_files": [
    "main.py",
    "webchat.py",
    "my_new_file.py",   # ← เพิ่มตรงนี้
],
```

---

## เปลี่ยนเวอร์ชัน

```python
"app_version": "1.1.0",
```

---

## Output

```
build_system/dist/
├── GemmaAI_Setup.exe            ← ไฟล์ติดตั้ง (แจกให้ผู้ใช้)
└── GemmaAI_Setup_v1.0.1.zip    ← zip พร้อม README
```

---

## การทำงานของ .exe เมื่อผู้ใช้รัน

```
1. Extract ไฟล์โปรแกรมไปที่ %APPDATA%\Local\GemmaAI\_src\
2. เปิด GUI Setup Wizard (Tkinter)
3. ผู้ใช้เลือก install path, model, ตั้งค่า Line Bot
4. Copy ไฟล์ไปยัง install path ที่เลือก
5. เสร็จ → เปิด Launcher ได้เลย
```

---

## Requirements

- Python 3.10+
- Windows (สำหรับ build .exe)
- PyInstaller และ Pillow (ติดตั้งอัตโนมัติเมื่อรัน build.bat)

---

## แก้ปัญหาที่พบบ่อย

| ปัญหา | วิธีแก้ |
|---|---|
| `ไม่พบ setup_wizard.py` | ตรวจสอบว่า `build_system/` อยู่ภายใน `final_package/` |
| Build ล้มเหลว ไม่มี error ชัดเจน | รัน `python build.py` ใน terminal ดู output ทั้งหมด |
| .exe เปิดแล้วปิดทันที | ลองเปลี่ยน `"windowed": False` ชั่วคราวเพื่อดู error |
| ไฟล์บาง bundle ไม่ถูก copy | ตรวจสอบรายการ `bundle_files` ให้ตรงกับ `_copy_files_to_install_dir()` ใน setup_wizard.py |