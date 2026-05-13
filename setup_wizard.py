# -*- coding: utf-8 -*-
"""
setup_wizard.py — Gemma AI Setup Wizard v2
Features:
- เลือก install path
- เลือก AI model หลายตัว
- Uninstall option
- Dark theme UI
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.scrolledtext as scrolledtext
import subprocess, threading, sys, os, shutil, webbrowser, json
from datetime import datetime

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, ".wizard_config.json")

# ── Colors ─────────────────────────────────────────────────────────────────
BG = "#0d0f14"; SURFACE = "#151821"; SURFACE2 = "#1c2030"; BORDER = "#252a3d"
ACCENT = "#6c8fff"; ACCENT2 = "#a78bfa"; GREEN = "#4ade80"; RED = "#f87171"
YELLOW = "#fbbf24"; TEXT = "#e2e8f8"; MUTED = "#6b7280"

# ── Available Models ────────────────────────────────────────────────────────
MODELS = [
    {"id": "gemma3:9b",   "name": "Gemma 3 9B",   "size": "~5.5 GB", "ram": "8 GB+",  "vision": False, "desc": "แนะนำ — ฉลาด สมดุลระหว่างความเร็วและความแม่นยำ"},
    {"id": "gemma3:4b",   "name": "Gemma 3 4B",   "size": "~3.3 GB", "ram": "6 GB+",  "vision": False, "desc": "เร็วกว่า — เหมาะกับเครื่องที่ RAM น้อย"},
    {"id": "gemma4:e4b",  "name": "Gemma 4 4B",   "size": "~3.3 GB", "ram": "6 GB+",  "vision": True,  "desc": "รองรับรูปภาพ Vision — Gemma รุ่นใหม่ล่าสุด"},
    {"id": "llama3.2:3b", "name": "Llama 3.2 3B", "size": "~2.0 GB", "ram": "4 GB+",  "vision": False, "desc": "เล็กมาก — เร็วสุด เหมาะกับ RAM น้อย"},
    {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "size": "~4.9 GB", "ram": "8 GB+",  "vision": False, "desc": "ฉลาด — ภาษาอังกฤษดีมาก"},
    {"id": "qwen2.5:7b",  "name": "Qwen 2.5 7B",  "size": "~4.7 GB", "ram": "8 GB+",  "vision": False, "desc": "ดีภาษาไทยและจีน"},
    {"id": "mistral:7b",  "name": "Mistral 7B",   "size": "~4.1 GB", "ram": "8 GB+",  "vision": False, "desc": "เร็วและฉลาด — ยอดนิยม"},
    {"id": "custom",      "name": "กำหนดเอง...",  "size": "—",       "ram": "—",      "vision": False, "desc": "พิมพ์ชื่อ model จาก ollama.com/library"},
]


class SetupWizard:
    def __init__(self, root):
        self.root       = root
        self.step       = 0
        self.install_dir = tk.StringVar(value=BASE_DIR)
        self.model_var   = tk.StringVar(value="gemma3:9b")
        self.custom_model = tk.StringVar(value="")
        self.line_token  = tk.StringVar(value="")
        self.line_secret = tk.StringVar(value="")
        self.web_port    = tk.StringVar(value="8001")
        self.installed   = self._load_config()

        self.steps = [
            ("ยินดีต้อนรับ",        self.step_welcome),
            ("เลือกโฟลเดอร์",       self.step_path),
            ("AI Model",             self.step_model),
            ("ติดตั้ง Dependencies", self.step_deps),
            ("Ollama & Model",       self.step_ollama),
            ("ตั้งค่า Line Bot",     self.step_line),
            ("เสร็จสิ้น",            self.step_done),
        ]
        self._build_shell()
        self.show_step(0)

    # ── Config ─────────────────────────────────────────────────────────────
    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                return json.load(open(CONFIG_FILE, encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_config(self, data: dict):
        cfg = self._load_config()
        cfg.update(data)
        json.dump(cfg, open(CONFIG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    def _set_env(self, key, value, path=None):
        target = path or self.install_dir.get()
        os.makedirs(target, exist_ok=True)  # สร้างโฟลเดอร์ถ้ายังไม่มี
        env_path = os.path.join(target, ".env")
        lines, found = [], False
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                if line.startswith(f"{key}="):
                    lines.append(f"{key}={value}\n"); found = True
                else:
                    lines.append(line)
        if not found:
            lines.append(f"{key}={value}\n")
        open(env_path, "w", encoding="utf-8").writelines(lines)

    # ── Shell ───────────────────────────────────────────────────────────────
    def _build_shell(self):
        self.root.title("Gemma AI — Setup Wizard")
        self.root.geometry("700x640")
        self.root.resizable(True, True)
        self.root.configure(bg=BG)
        self.root.minsize(640, 560)

        # Top bar
        top = tk.Frame(self.root, bg=SURFACE, height=70)
        top.pack(fill="x"); top.pack_propagate(False)

        tk.Label(top, text="🤖  Gemma AI Setup", bg=SURFACE, fg=TEXT,
                font=("Segoe UI", 15, "bold")).pack(side="left", padx=20, pady=18)

        self.step_lbl = tk.Label(top, text="", bg=SURFACE, fg=MUTED, font=("Segoe UI", 9))
        self.step_lbl.pack(side="right", padx=20)

        # Sidebar steps
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(body, bg=SURFACE, width=160)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.sidebar_labels = []
        for i, (name, _) in enumerate(self.steps):
            lbl = tk.Label(self.sidebar, text=f"  {i+1}. {name}", bg=SURFACE, fg=MUTED,
                          font=("Segoe UI", 9), anchor="w", cursor="hand2")
            lbl.pack(fill="x", pady=1, ipady=7)
            self.sidebar_labels.append(lbl)

        # Uninstall button in sidebar
        tk.Frame(self.sidebar, bg=BORDER, height=1).pack(fill="x", pady=8)
        tk.Button(self.sidebar, text="🗑 ถอนการติดตั้ง", bg=SURFACE, fg=RED,
                 font=("Segoe UI", 9), relief="flat", bd=0, cursor="hand2",
                 command=self.uninstall).pack(fill="x", ipady=6)

        # Content
        self.content = tk.Frame(body, bg=BG, padx=28, pady=20)
        self.content.pack(side="left", fill="both", expand=True)

        # Footer
        footer = tk.Frame(self.root, bg=SURFACE, pady=10)
        footer.pack(fill="x")

        self.btn_back = tk.Button(footer, text="← ย้อนกลับ", bg=SURFACE2, fg=MUTED,
                                   font=("Segoe UI", 10), relief="flat", padx=16, pady=7,
                                   cursor="hand2", command=self.go_back)
        self.btn_back.pack(side="left", padx=16)

        self.btn_skip = tk.Button(footer, text="ข้าม →", bg=SURFACE2, fg=MUTED,
                                   font=("Segoe UI", 10), relief="flat", padx=14, pady=7,
                                   cursor="hand2", command=self.go_next)
        self.btn_skip.pack(side="right", padx=4)

        self.btn_next = tk.Button(footer, text="ถัดไป →", bg=ACCENT, fg="white",
                                   font=("Segoe UI", 10, "bold"), relief="flat", padx=20, pady=7,
                                   cursor="hand2", command=self.go_next)
        self.btn_next.pack(side="right", padx=16)

    # ── Navigation ──────────────────────────────────────────────────────────
    def clear(self):
        for w in self.content.winfo_children():
            w.destroy()

    def show_step(self, n):
        self.step = n
        self.clear()
        # sidebar highlight
        for i, lbl in enumerate(self.sidebar_labels):
            if i == n:
                lbl.config(fg=TEXT, bg=SURFACE2, font=("Segoe UI", 9, "bold"))
            elif i < n:
                lbl.config(fg=GREEN, bg=SURFACE, font=("Segoe UI", 9))
            else:
                lbl.config(fg=MUTED, bg=SURFACE, font=("Segoe UI", 9))
        self.step_lbl.config(text=f"ขั้นตอน {n+1} / {len(self.steps)}")
        self.btn_back.config(state="normal" if n > 0 else "disabled")
        self.btn_next.config(text="ถัดไป →", bg=ACCENT, state="normal")
        self.btn_skip.config(text="ข้าม →", state="normal")
        self.steps[n][1]()

    def go_next(self):
        if self.step < len(self.steps) - 1:
            self.show_step(self.step + 1)

    def go_back(self):
        if self.step > 0:
            self.show_step(self.step - 1)

    # ── UI Helpers ──────────────────────────────────────────────────────────
    def H1(self, text):
        tk.Label(self.content, text=text, bg=BG, fg=TEXT,
                font=("Segoe UI", 14, "bold"), anchor="w").pack(fill="x", pady=(0, 4))

    def Sub(self, text):
        tk.Label(self.content, text=text, bg=BG, fg=MUTED,
                font=("Segoe UI", 10), anchor="w", wraplength=480, justify="left").pack(fill="x", pady=(0, 14))

    def Card(self, parent=None):
        f = tk.Frame(parent or self.content, bg=SURFACE, pady=10, padx=14)
        f.pack(fill="x", pady=3)
        return f

    def LogBox(self, height=8):
        box = scrolledtext.ScrolledText(
            self.content, height=height, bg="#0d1117", fg="#c9d1d9",
            font=("Consolas", 9), relief="flat", bd=0, state="disabled")
        box.pack(fill="both", expand=True, pady=(6, 0))
        box.tag_config("g", foreground=GREEN)
        box.tag_config("r", foreground=RED)
        box.tag_config("y", foreground=YELLOW)
        box.tag_config("m", foreground=MUTED)
        return box

    def log(self, box, msg, tag=""):
        def _do():
            box.config(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            box.insert("end", f"[{ts}] {msg}\n", tag)
            box.see("end")
            box.config(state="disabled")
        self.root.after(0, _do)

    def Entry(self, parent, var, show=None, placeholder=""):
        e = tk.Entry(parent, textvariable=var, bg=SURFACE2, fg=TEXT,
                    font=("Segoe UI", 10), relief="flat", bd=0,
                    insertbackground=ACCENT, show=show or "")
        e.pack(fill="x", ipady=8, pady=(3, 0))
        return e

    def Btn(self, parent, text, cmd, color=ACCENT, fg="white", side="left"):
        b = tk.Button(parent, text=text, bg=color, fg=fg,
                     font=("Segoe UI", 9, "bold"), relief="flat", padx=12, pady=6,
                     cursor="hand2", command=cmd)
        b.pack(side=side, padx=(0, 6), pady=(6, 0))
        return b

    # ── Step 0: Welcome ─────────────────────────────────────────────────────
    def step_welcome(self):
        self.H1("ยินดีต้อนรับสู่ Gemma AI")
        self.Sub("Wizard นี้จะช่วยตั้งค่าระบบให้ครบในไม่กี่ขั้นตอน")

        items = [
            (ACCENT,  "📁", "เลือกโฟลเดอร์ที่ต้องการติดตั้ง"),
            (ACCENT,  "🤖", "เลือก AI Model ที่เหมาะกับเครื่อง"),
            (ACCENT,  "📦", "ติดตั้ง dependencies อัตโนมัติ"),
            (ACCENT,  "⬇", "ดาวน์โหลด Ollama และ AI Model"),
            (ACCENT,  "💬", "ตั้งค่า Line Bot (ไม่บังคับ)"),
            (GREEN,   "✅", "พร้อมใช้งานทันที"),
        ]
        for color, ico, text in items:
            row = self.Card()
            tk.Label(row, text=ico, bg=SURFACE, fg=color, font=("Segoe UI", 13)).pack(side="left", padx=(0,12))
            tk.Label(row, text=text, bg=SURFACE, fg=TEXT, font=("Segoe UI", 10)).pack(side="left")

        if self.installed.get("installed"):
            note = tk.Frame(self.content, bg="#1a1a0a", pady=8, padx=14)
            note.pack(fill="x", pady=(10, 0))
            tk.Label(note, text=f"⚠ ตรวจพบการติดตั้งเดิม ({self.installed.get('date','')})",
                    bg="#1a1a0a", fg=YELLOW, font=("Segoe UI", 10)).pack(anchor="w")
            tk.Label(note, text="สามารถกด 'ถอนการติดตั้ง' ในเมนูซ้ายได้ตลอดเวลา",
                    bg="#1a1a0a", fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")

    # ── Step 1: Install Path ─────────────────────────────────────────────
    def step_path(self):
        self.H1("เลือกโฟลเดอร์ติดตั้ง")
        self.Sub("เลือกโฟลเดอร์ที่ต้องการวางไฟล์ทั้งหมด")

        card = self.Card()
        tk.Label(card, text="โฟลเดอร์ปัจจุบัน:", bg=SURFACE, fg=MUTED,
                font=("Segoe UI", 9)).pack(anchor="w")

        path_frame = tk.Frame(card, bg=SURFACE)
        path_frame.pack(fill="x", pady=(4, 0))

        path_entry = tk.Entry(path_frame, textvariable=self.install_dir,
                             bg=SURFACE2, fg=TEXT, font=("Segoe UI", 10),
                             relief="flat", bd=0, insertbackground=ACCENT)
        path_entry.pack(side="left", fill="x", expand=True, ipady=8)

        tk.Button(path_frame, text="เลือก...", bg=ACCENT, fg="white",
                 font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                 cursor="hand2", command=self._browse_path).pack(side="left", padx=(6, 0))

        # Presets
        tk.Label(self.content, text="หรือเลือกจากตัวเลือกด่วน:", bg=BG, fg=MUTED,
                font=("Segoe UI", 9)).pack(anchor="w", pady=(14, 4))

        presets = [
            ("Desktop", os.path.join(os.path.expanduser("~"), "Desktop", "GemmaAI")),
            ("Documents", os.path.join(os.path.expanduser("~"), "Documents", "GemmaAI")),
            ("C:\\GemmaAI", "C:\\GemmaAI"),
            ("โฟลเดอร์นี้", BASE_DIR),
        ]
        prow = tk.Frame(self.content, bg=BG)
        prow.pack(fill="x")
        for label, path in presets:
            tk.Button(prow, text=label, bg=SURFACE2, fg=TEXT,
                     font=("Segoe UI", 9), relief="flat", padx=10, pady=5,
                     cursor="hand2",
                     command=lambda p=path: self.install_dir.set(p)).pack(side="left", padx=(0, 6))

        # Space warning
        note = self.Card()
        tk.Label(note, text="⚠ พื้นที่ที่ต้องการ:", bg=SURFACE, fg=YELLOW,
                font=("Segoe UI", 9, "bold")).pack(anchor="w")
        for line in ["• ไฟล์โปรแกรม: ~10 MB",
                     "• AI Model (Gemma 9B): ~6 GB",
                     "• แนะนำพื้นที่ว่างอย่างน้อย 10 GB"]:
            tk.Label(note, text=line, bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")

    def _browse_path(self):
        path = filedialog.askdirectory(title="เลือกโฟลเดอร์ติดตั้ง")
        if path:
            self.install_dir.set(os.path.join(path, "GemmaAI"))

    # ── Step 2: Model Selection ───────────────────────────────────────────
    def step_model(self):
        self.H1("เลือก AI Model")
        self.Sub("เลือก model ที่เหมาะกับ RAM และความต้องการของคุณ")

        # RAM guide
        guide = self.Card()
        tk.Label(guide, text="แนะนำตามขนาด RAM:", bg=SURFACE, fg=MUTED,
                font=("Segoe UI", 9, "bold")).pack(anchor="w")
        guides = [("4 GB RAM", "Llama 3.2 3B", RED),
                  ("6 GB RAM", "Gemma 3 4B หรือ Gemma 4 4B", YELLOW),
                  ("8 GB RAM+", "Gemma 3 9B (แนะนำ)", GREEN)]
        for ram, rec, color in guides:
            row = tk.Frame(guide, bg=SURFACE)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"  {ram}:", bg=SURFACE, fg=MUTED, width=12, anchor="w",
                    font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row, text=rec, bg=SURFACE, fg=color,
                    font=("Segoe UI", 9, "bold")).pack(side="left")

        # Model list in scrollable frame
        tk.Label(self.content, text="เลือก Model:", bg=BG, fg=TEXT,
                font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(12, 4))

        # Scrollable container
        scroll_container = tk.Frame(self.content, bg=BG)
        scroll_container.pack(fill="both", expand=True)

        canvas = tk.Canvas(scroll_container, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        list_frame = tk.Frame(canvas, bg=BG)
        canvas_window = canvas.create_window((0, 0), window=list_frame, anchor="nw")

        def on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def on_canvas_configure(e):
            canvas.itemconfig(canvas_window, width=e.width)
        def on_mousewheel(e):
            canvas.yview_scroll(int(-1*(e.delta/120)), "units")

        list_frame.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_canvas_configure)
        canvas.bind("<MouseWheel>", on_mousewheel)
        list_frame.bind("<MouseWheel>", on_mousewheel)

        self._model_cards = {}

        def select_model(model_id):
            self.model_var.set(model_id)
            for mid, widgets in self._model_cards.items():
                is_sel = mid == model_id
                bg = "#1a2040" if is_sel else SURFACE
                bd = ACCENT if is_sel else SURFACE
                for w in widgets:
                    try: w.config(bg=bg)
                    except Exception: pass
                widgets[0].config(highlightbackground=bd, highlightthickness=2 if is_sel else 0)

        for m in MODELS:
            card_bg = SURFACE
            row = tk.Frame(list_frame, bg=card_bg, pady=8, padx=12, cursor="hand2",
                          highlightthickness=0)
            row.pack(fill="x", pady=3)

            left = tk.Frame(row, bg=card_bg)
            left.pack(side="left", fill="x", expand=True)

            top_row = tk.Frame(left, bg=card_bg)
            top_row.pack(fill="x")

            name_lbl = tk.Label(top_row, text=m["name"], bg=card_bg, fg=TEXT,
                    font=("Segoe UI", 11, "bold"))
            name_lbl.pack(side="left")

            if m["vision"]:
                vis_lbl = tk.Label(top_row, text="  👁 Vision", bg=card_bg, fg=ACCENT2,
                        font=("Segoe UI", 9))
                vis_lbl.pack(side="left")
            else:
                vis_lbl = None

            desc_lbl = tk.Label(left, text=m["desc"], bg=card_bg, fg=MUTED,
                    font=("Segoe UI", 9))
            desc_lbl.pack(anchor="w", pady=(2,0))

            right = tk.Frame(row, bg=card_bg)
            right.pack(side="right", padx=(8,0))

            size_lbl = tk.Label(right, text=f"{m['size']}  {m['ram']}",
                    bg=card_bg, fg=MUTED, font=("Segoe UI", 9), justify="right")
            size_lbl.pack(anchor="e")

            all_widgets = [row, left, top_row, name_lbl, desc_lbl, right, size_lbl]
            if vis_lbl: all_widgets.append(vis_lbl)
            self._model_cards[m["id"]] = all_widgets

            # bind คลิกทุก widget ใน card
            def make_click(mid): return lambda e: select_model(mid)
            def make_enter(r, mid):
                return lambda e: r.config(bg=SURFACE2) if self.model_var.get() != mid else None
            def make_leave(r, mid):
                return lambda e: r.config(bg="#1a2040" if self.model_var.get()==mid else SURFACE)
            for w in all_widgets:
                w.bind("<Button-1>", make_click(m["id"]))
                w.bind("<Enter>",    make_enter(row, m["id"]))
                w.bind("<Leave>",    make_leave(row, m["id"]))
                w.bind("<MouseWheel>", on_mousewheel)

        # เลือก default
        select_model(self.model_var.get())

        # Custom model - อยู่ใน list_frame (scroll ได้)
        custom_row = tk.Frame(list_frame, bg=SURFACE, pady=8, padx=12, cursor="hand2")
        custom_row.pack(fill="x", pady=3)
        custom_row.bind("<MouseWheel>", on_mousewheel)

        tk.Label(custom_row, text="✏  กำหนด Model เอง", bg=SURFACE, fg=TEXT,
                font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(custom_row, text="พิมพ์ชื่อ model จาก ollama.com/library เช่น phi4:latest, deepseek-r1:7b",
                bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        custom_entry = tk.Entry(custom_row, textvariable=self.custom_model,
                               bg=SURFACE2, fg=TEXT, font=("Segoe UI", 10),
                               relief="flat", bd=0, insertbackground=ACCENT)
        custom_entry.pack(fill="x", ipady=7, pady=(6, 0))

        def on_custom_click(e=None):
            self.model_var.set("custom")
            # reset all other cards
            for mid, widgets in self._model_cards.items():
                for w in widgets:
                    try: w.config(bg=SURFACE)
                    except: pass
                widgets[0].config(highlightthickness=0)
            custom_row.config(bg="#1a2040", highlightbackground=ACCENT, highlightthickness=2)
        custom_row.bind("<Button-1>", on_custom_click)
        custom_entry.bind("<FocusIn>", on_custom_click)

    # ── Step 3: Install Dependencies ─────────────────────────────────────
    def step_deps(self):
        self.H1("ติดตั้ง Python Packages")
        self.Sub("กด 'ติดตั้ง' เพื่อดาวน์โหลดและติดตั้ง packages ที่จำเป็น")

        self.btn_next.config(state="disabled")
        self.dep_box = self.LogBox(height=10)

        btn_row = tk.Frame(self.content, bg=BG)
        btn_row.pack(fill="x", pady=(6, 0))
        self.Btn(btn_row, "▶ ติดตั้ง Packages", self._install_deps, color="#166534")
        self.Btn(btn_row, "ข้าม (มีแล้ว)", self.go_next, color=SURFACE2, fg=MUTED)

    def _install_deps(self):
        pkgs = [
            ("fastapi>=0.111.0",        "FastAPI"),
            ("uvicorn[standard]>=0.30.0","Uvicorn"),
            ("httpx>=0.27.0",           "HTTPX"),
            ("line-bot-sdk>=3.11.0",    "Line Bot SDK"),
            ("python-dotenv>=1.0.0",    "dotenv"),
            ("pypdf>=4.0.0",            "pypdf"),
            ("python-docx>=1.1.0",      "python-docx"),
            ("openpyxl>=3.1.0",         "openpyxl"),
            ("python-multipart>=0.0.9", "multipart"),
        ]
        def run():
            all_ok = True
            for pkg, name in pkgs:
                self.log(self.dep_box, f"ติดตั้ง {name}...", "m")
                r = subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q"],
                                   capture_output=True, text=True)
                if r.returncode == 0:
                    self.log(self.dep_box, f"✓ {name}", "g")
                else:
                    self.log(self.dep_box, f"✗ {name}: {r.stderr[:60]}", "r")
                    all_ok = False

            if all_ok:
                self.log(self.dep_box, "\n✓ ติดตั้งครบแล้ว!", "g")
            else:
                self.log(self.dep_box, "\n⚠ บางตัวล้มเหลว ลองรัน pip install ด้วยตัวเอง", "y")

            self.root.after(0, lambda: self.btn_next.config(state="normal"))
        threading.Thread(target=run, daemon=True).start()

    # ── Step 4: Ollama & Pull Model ───────────────────────────────────────
    def step_ollama(self):
        self.H1("ติดตั้ง Ollama และ Pull Model")

        # Check ollama
        r = subprocess.run(["ollama", "--version"], capture_output=True, text=True)
        ollama_ok = r.returncode == 0

        status = self.Card()
        if ollama_ok:
            tk.Label(status, text=f"✓ Ollama พร้อมใช้งาน ({r.stdout.strip()})",
                    bg=SURFACE, fg=GREEN, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        else:
            tk.Label(status, text="✗ ไม่พบ Ollama — ต้องติดตั้งก่อน",
                    bg=SURFACE, fg=RED, font=("Segoe UI", 10, "bold")).pack(anchor="w")
            row = tk.Frame(status, bg=SURFACE)
            row.pack(fill="x", pady=(6, 0))
            self.Btn(row, "⬇ ดาวน์โหลด Ollama",
                    lambda: webbrowser.open("https://ollama.com/download"), color=ACCENT)
            self.Btn(row, "ตรวจสอบใหม่", self._recheck_ollama, color=SURFACE2, fg=TEXT)

        # Selected model info
        model_id = self.custom_model.get().strip() if self.model_var.get() == "custom" else self.model_var.get()
        m = next((x for x in MODELS if x["id"] == model_id), None)
        model_card = self.Card()
        tk.Label(model_card, text=f"Model ที่เลือก: {model_id}",
                bg=SURFACE, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        if m:
            tk.Label(model_card, text=f"ขนาด: {m['size']}  |  RAM: {m['ram']}",
                    bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        # Check if already pulled
        pulled = False
        try:
            r2 = subprocess.run(["ollama", "list"], capture_output=True, text=True)
            if model_id.split(":")[0] in r2.stdout:
                pulled = True
        except Exception:
            pass

        if pulled:
            pull_card = self.Card()
            tk.Label(pull_card, text=f"✓ {model_id} มีอยู่แล้ว ไม่ต้อง pull ใหม่",
                    bg=SURFACE, fg=GREEN, font=("Segoe UI", 10)).pack(anchor="w")

        self.ollama_log = self.LogBox(height=7)

        btn_row = tk.Frame(self.content, bg=BG)
        btn_row.pack(fill="x", pady=(6, 0))

        if not pulled:
            self.Btn(btn_row, f"⬇ Pull {model_id}", self._pull_model, color="#166534")
        self.Btn(btn_row, "ข้ามขั้นตอนนี้", self.go_next, color=SURFACE2, fg=MUTED)

        if ollama_ok:
            self.btn_next.config(state="normal")
        else:
            self.btn_next.config(state="disabled")

    def _recheck_ollama(self):
        r = subprocess.run(["ollama", "--version"], capture_output=True, text=True)
        if r.returncode == 0:
            messagebox.showinfo("พบ Ollama", f"✓ Ollama พร้อมใช้งาน\n{r.stdout.strip()}")
            self.show_step(self.step)
        else:
            messagebox.showerror("ไม่พบ Ollama", "ยังไม่พบ Ollama กรุณาติดตั้งและลองใหม่")

    def _pull_model(self):
        model_id = self.custom_model.get().strip() if self.model_var.get() == "custom" else self.model_var.get()
        self.log(self.ollama_log, f"กำลัง pull {model_id} ... (อาจใช้เวลา 5-20 นาที)", "y")
        self.btn_next.config(state="disabled")

        def run():
            proc = subprocess.Popen(
                ["ollama", "pull", model_id],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace"
            )
            for line in proc.stdout:
                line = line.strip()
                if line: self.log(self.ollama_log, f"  {line}", "m")
            proc.wait()
            if proc.returncode == 0:
                self.log(self.ollama_log, f"\n✓ Pull {model_id} สำเร็จ!", "g")
                self._set_env("OLLAMA_MODEL", model_id)
                self._set_env("OLLAMA_VISION_MODEL", model_id)
                self._save_config({"model": model_id})
                self.root.after(0, lambda: self.btn_next.config(state="normal"))
            else:
                self.log(self.ollama_log, f"\n✗ ล้มเหลว ลองรัน: ollama pull {model_id}", "r")
                self.root.after(0, lambda: self.btn_next.config(state="normal"))

        threading.Thread(target=run, daemon=True).start()

    # ── Step 5: Line Bot Config ───────────────────────────────────────────
    def step_line(self):
        self.H1("ตั้งค่า Line Bot (ไม่บังคับ)")
        self.Sub("ถ้าไม่ต้องการใช้ Line Bot กด 'ข้าม' ได้เลย")

        env_path = os.path.join(self.install_dir.get(), ".env")
        existing = {}
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    existing[k] = v

        self.line_token.set(existing.get("LINE_CHANNEL_ACCESS_TOKEN", ""))
        self.line_secret.set(existing.get("LINE_CHANNEL_SECRET", ""))
        self.web_port.set(existing.get("WEB_PORT", "8001"))

        fields = [
            ("LINE_CHANNEL_ACCESS_TOKEN", "Channel Access Token", self.line_token,
             "ยาวประมาณ 170+ ตัวอักษร — ได้จาก Line Console"),
            ("LINE_CHANNEL_SECRET",       "Channel Secret",       self.line_secret,
             "32 ตัวอักษร — ได้จาก Line Console"),
        ]
        for key, label, var, hint in fields:
            card = self.Card()
            tk.Label(card, text=label, bg=SURFACE, fg=TEXT,
                    font=("Segoe UI", 10, "bold")).pack(anchor="w")
            tk.Label(card, text=hint, bg=SURFACE, fg=MUTED,
                    font=("Segoe UI", 9)).pack(anchor="w")
            self.Entry(card, var)

        # Web port
        port_card = self.Card()
        tk.Label(port_card, text="Web Chat Port", bg=SURFACE, fg=TEXT,
                font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.Entry(port_card, self.web_port)

        # Line console link
        link = tk.Label(self.content, text="🔗 เปิด Line Developers Console",
                       bg=BG, fg=ACCENT, font=("Segoe UI", 9), cursor="hand2")
        link.pack(anchor="w", pady=(8, 0))
        link.bind("<Button-1>", lambda e: webbrowser.open("https://developers.line.biz/console/"))

        self.save_status = tk.Label(self.content, text="", bg=BG, fg=GREEN, font=("Segoe UI", 10))
        self.save_status.pack(anchor="w", pady=(4, 0))

        btn_row = tk.Frame(self.content, bg=BG)
        btn_row.pack(fill="x", pady=(8, 0))
        self.Btn(btn_row, "💾 บันทึก", self._save_line, color="#166534")

    def _save_line(self):
        token  = self.line_token.get().strip()
        secret = self.line_secret.get().strip()
        port   = self.web_port.get().strip() or "8001"
        path   = self.install_dir.get()
        os.makedirs(path, exist_ok=True)
        if token:  self._set_env("LINE_CHANNEL_ACCESS_TOKEN", token, path)
        if secret: self._set_env("LINE_CHANNEL_SECRET", secret, path)
        self._set_env("WEB_PORT", port, path)
        self._set_env("OLLAMA_BASE_URL", "http://localhost:11434", path)
        self.save_status.config(text="✓ บันทึกแล้ว!")

    # ── Step 6: Done ──────────────────────────────────────────────────────
    def step_done(self):
        # Save install record
        self._save_config({
            "installed": True,
            "date":      datetime.now().strftime("%Y-%m-%d %H:%M"),
            "path":      self.install_dir.get(),
            "model":     self.model_var.get(),
        })

        self.H1("🎉 ติดตั้งเสร็จสมบูรณ์!")
        self.Sub("Gemma AI พร้อมใช้งานแล้ว")

        model_id = self.custom_model.get().strip() if self.model_var.get() == "custom" else self.model_var.get()
        port     = self.web_port.get() or "8001"

        infos = [
            ("🌐", "Web Chat",   f"http://localhost:{port}"),
            ("🤖", "AI Model",   model_id),
            ("📁", "ติดตั้งที่", self.install_dir.get()),
        ]
        for ico, label, val in infos:
            card = self.Card()
            tk.Label(card, text=f"{ico}  {label}:", bg=SURFACE, fg=MUTED,
                    font=("Segoe UI", 9)).pack(anchor="w")
            tk.Label(card, text=val, bg=SURFACE, fg=TEXT,
                    font=("Segoe UI", 10, "bold")).pack(anchor="w")

        note = self.Card()
        tk.Label(note, text="ครั้งหน้าดับเบิ้ลคลิ๊ก start.bat ได้เลย ไม่ต้องติดตั้งซ้ำ",
                bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        # Copy files status
        if self.install_dir.get() != BASE_DIR:
            copy_card = self.Card()
            tk.Label(copy_card, text="📁 คัดลอกไฟล์โปรแกรม", bg=SURFACE, fg=MUTED,
                    font=("Segoe UI", 9)).pack(anchor="w")
            self.copy_status = tk.Label(copy_card, text="กำลังคัดลอก...", bg=SURFACE,
                                        fg=YELLOW, font=("Segoe UI", 9))
            self.copy_status.pack(anchor="w")

            def do_copy():
                copied, skipped = self._copy_files_to_install_dir()
                msg = f"✓ คัดลอก {len(copied)} ไฟล์แล้ว"
                if skipped:
                    msg += f"  (ข้าม {len(skipped)}: {', '.join(skipped[:3])})"
                self.root.after(0, lambda: self.copy_status.config(text=msg, fg=GREEN))

            threading.Thread(target=do_copy, daemon=True).start()

        btn_row = tk.Frame(self.content, bg=BG)
        btn_row.pack(fill="x", pady=(12, 0))
        self.Btn(btn_row, "🚀 เปิด Launcher", self._launch, color=GREEN, fg="#000")
        self.Btn(btn_row, "📂 เปิดโฟลเดอร์",
                lambda: os.startfile(self.install_dir.get()),
                color=SURFACE2, fg=TEXT)
        self.Btn(btn_row, "🌐 เปิด Web Chat",
                lambda: webbrowser.open(f"http://localhost:{port}"),
                color=ACCENT)

        self.btn_next.config(state="disabled")
        self.btn_skip.config(state="disabled")

    def _copy_files_to_install_dir(self):
        """คัดลอกไฟล์โปรแกรมทั้งหมดไปยัง install path"""
        dest = self.install_dir.get()
        os.makedirs(dest, exist_ok=True)

        # ไฟล์ที่ต้องคัดลอก
        files_to_copy = [
            "main.py", "webchat.py", "file_processor.py",
            "conversation.py", "database.py", "launcher.py",
            "run.py", "requirements.txt", ".env.example",
            ".gitignore", "start.bat", "install.bat",
        ]

        copied, skipped = [], []
        for fname in files_to_copy:
            src = os.path.join(BASE_DIR, fname)
            dst = os.path.join(dest, fname)
            if os.path.exists(src):
                try:
                    shutil.copy2(src, dst)
                    copied.append(fname)
                except Exception as e:
                    skipped.append(f"{fname} ({e})")
            else:
                skipped.append(f"{fname} (ไม่พบ)")

        return copied, skipped

    def _launch(self):
        # คัดลอกไฟล์ไปก่อน
        dest = self.install_dir.get()
        if dest != BASE_DIR:
            copied, skipped = self._copy_files_to_install_dir()

        launcher = os.path.join(dest, "launcher.py")
        if not os.path.exists(launcher):
            launcher = os.path.join(BASE_DIR, "launcher.py")

        if os.path.exists(launcher):
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            subprocess.Popen([sys.executable, launcher],
                           cwd=os.path.dirname(launcher), env=env)
        self.root.destroy()

    # ── Uninstall ──────────────────────────────────────────────────────────
    def uninstall(self):
        ans = messagebox.askyesnocancel(
            "ถอนการติดตั้ง",
            "ต้องการถอนการติดตั้ง Gemma AI?\n\n"
            "กด Yes  = ลบไฟล์โปรแกรม (ไม่ลบ .env และฐานข้อมูล)\n"
            "กด No   = ลบทุกอย่างรวมถึงข้อมูล\n"
            "กด Cancel = ยกเลิก"
        )
        if ans is None: return

        keep_data = ans  # True = keep .env & db
        path = self.install_dir.get()

        if not os.path.exists(path):
            messagebox.showinfo("เสร็จแล้ว", "ไม่พบโฟลเดอร์ติดตั้ง อาจถูกลบไปแล้ว")
            return

        # ไฟล์ที่จะเก็บไว้ถ้า keep_data
        keep = {".env", "chat_history.db", ".wizard_config.json"} if keep_data else set()

        removed, skipped = [], []
        for f in os.listdir(path):
            full = os.path.join(path, f)
            if f in keep:
                skipped.append(f)
                continue
            try:
                if os.path.isfile(full):
                    os.remove(full)
                    removed.append(f)
                elif os.path.isdir(full):
                    shutil.rmtree(full)
                    removed.append(f)
            except Exception as e:
                skipped.append(f"{f} ({e})")

        # ลบ config
        if os.path.exists(CONFIG_FILE) and not keep_data:
            os.remove(CONFIG_FILE)

        msg = f"ถอนการติดตั้งเสร็จแล้ว\n\nลบ {len(removed)} ไฟล์"
        if skipped:
            msg += f"\nเก็บไว้: {', '.join(skipped)}"

        messagebox.showinfo("เสร็จแล้ว", msg)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app  = SetupWizard(root)
    root.mainloop()