"""
launcher.py — GUI Launcher สำหรับ Gemma AI
ดับเบิ้ลคลิ๊กได้เลย มีปุ่มเปิด/ปิดแต่ละส่วน
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import threading
import sys
import os
import time
import webbrowser
import socket
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_PORT = os.getenv("WEB_PORT", "8001")


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


class GemmaLauncher:
    def __init__(self, root):
        self.root       = root
        self.proc_web   = None
        self.proc_line  = None
        self.proc_ngrok = None
        self.local_ip   = get_local_ip()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log("🚀 Gemma AI Launcher พร้อมใช้งาน")

    # ── UI ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.root.title("Gemma AI Launcher")
        self.root.geometry("700x580")
        self.root.resizable(True, True)
        self.root.configure(bg="#0d0f14")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame",       background="#0d0f14")
        style.configure("Card.TFrame",  background="#151821", relief="flat")
        style.configure("TLabel",       background="#0d0f14", foreground="#e2e8f8", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#0d0f14", foreground="#e2e8f8", font=("Segoe UI", 14, "bold"))
        style.configure("Sub.TLabel",   background="#0d0f14", foreground="#6b7280", font=("Segoe UI", 9))
        style.configure("Card.TLabel",  background="#151821", foreground="#e2e8f8", font=("Segoe UI", 10))
        style.configure("Green.TLabel", background="#151821", foreground="#4ade80", font=("Segoe UI", 9, "bold"))
        style.configure("Red.TLabel",   background="#151821", foreground="#f87171", font=("Segoe UI", 9, "bold"))

        # Header
        hdr = ttk.Frame(self.root, style="TFrame", padding=(20, 16, 20, 8))
        hdr.pack(fill="x")
        ttk.Label(hdr, text="🤖  Gemma AI Launcher", style="Title.TLabel").pack(side="left")
        ttk.Label(hdr, text=f"Local AI · {self.local_ip}", style="Sub.TLabel").pack(side="right", pady=(4, 0))

        ttk.Separator(self.root).pack(fill="x", padx=20)

        # Cards
        cards = ttk.Frame(self.root, style="TFrame", padding=(16, 12))
        cards.pack(fill="x")

        self._web_card  = self._make_card(cards, 0,
            icon="🌐", title="Web Chat",
            desc=f"http://localhost:{WEB_PORT}",
            start_cmd=self.start_web,
            stop_cmd=self.stop_web,
            open_cmd=lambda: webbrowser.open(f"http://localhost:{WEB_PORT}"),
        )
        self._line_card = self._make_card(cards, 1,
            icon="💬", title="Line Bot",
            desc="http://localhost:8000",
            start_cmd=self.start_line,
            stop_cmd=self.stop_line,
            open_cmd=lambda: webbrowser.open("http://localhost:8000/health"),
        )
        self._ngrok_card = self._make_card(cards, 2,
            icon="🔗", title="ngrok Tunnel",
            desc="เชื่อม Line Bot กับอินเทอร์เน็ต",
            start_cmd=self.start_ngrok,
            stop_cmd=self.stop_ngrok,
            open_cmd=lambda: webbrowser.open("http://localhost:4040"),
        )

        # Quick links
        links = ttk.Frame(self.root, style="TFrame", padding=(16, 4))
        links.pack(fill="x")
        ttk.Label(links, text="เปิดด่วน:", style="Sub.TLabel").pack(side="left", padx=(0, 8))
        for label, url in [
            ("Web Chat", f"http://localhost:{WEB_PORT}"),
            (f"มือถือ ({self.local_ip})", f"http://{self.local_ip}:{WEB_PORT}"),
            ("ngrok UI", "http://localhost:4040"),
        ]:
            btn = tk.Button(links, text=label, bg="#1c2030", fg="#6c8fff",
                           relief="flat", bd=0, cursor="hand2",
                           font=("Segoe UI", 9), padx=8, pady=3,
                           command=lambda u=url: webbrowser.open(u))
            btn.pack(side="left", padx=3)

        ttk.Separator(self.root).pack(fill="x", padx=16, pady=(8, 0))

        # Log
        log_frame = ttk.Frame(self.root, style="TFrame", padding=(16, 8))
        log_frame.pack(fill="both", expand=True)
        ttk.Label(log_frame, text="📋 Log", style="Sub.TLabel").pack(anchor="w")

        self.log_box = scrolledtext.ScrolledText(
            log_frame, height=12, bg="#0d1117", fg="#c9d1d9",
            font=("Consolas", 9), relief="flat", bd=0,
            insertbackground="#6c8fff", state="disabled",
        )
        self.log_box.pack(fill="both", expand=True, pady=(4, 0))
        self.log_box.tag_config("green",  foreground="#4ade80")
        self.log_box.tag_config("red",    foreground="#f87171")
        self.log_box.tag_config("yellow", foreground="#fbbf24")
        self.log_box.tag_config("cyan",   foreground="#67e8f9")
        self.log_box.tag_config("blue",   foreground="#60a5fa")
        self.log_box.tag_config("muted",  foreground="#6b7280")

        # Bottom bar
        bot = ttk.Frame(self.root, style="TFrame", padding=(16, 8))
        bot.pack(fill="x")
        tk.Button(bot, text="🚀 เริ่มทั้งหมด", bg="#6c8fff", fg="white",
                 font=("Segoe UI", 10, "bold"), relief="flat", padx=16, pady=6,
                 cursor="hand2", command=self.start_all).pack(side="left", padx=(0, 8))
        tk.Button(bot, text="⏹ หยุดทั้งหมด", bg="#374151", fg="#e2e8f8",
                 font=("Segoe UI", 10), relief="flat", padx=16, pady=6,
                 cursor="hand2", command=self.stop_all).pack(side="left")
        tk.Button(bot, text="🗑 ล้าง Log", bg="#1c2030", fg="#6b7280",
                 font=("Segoe UI", 9), relief="flat", padx=12, pady=6,
                 cursor="hand2", command=self.clear_log).pack(side="right")

    def _make_card(self, parent, row, icon, title, desc, start_cmd, stop_cmd, open_cmd):
        frame = tk.Frame(parent, bg="#151821", bd=1, relief="flat")
        frame.grid(row=row, column=0, sticky="ew", pady=4)
        parent.columnconfigure(0, weight=1)

        inner = tk.Frame(frame, bg="#151821", padx=14, pady=10)
        inner.pack(fill="x")

        left = tk.Frame(inner, bg="#151821")
        left.pack(side="left", fill="x", expand=True)

        tk.Label(left, text=f"{icon}  {title}", bg="#151821", fg="#e2e8f8",
                font=("Segoe UI", 11, "bold")).pack(anchor="w")

        desc_var = tk.StringVar(value=desc)
        tk.Label(left, textvariable=desc_var, bg="#151821", fg="#6b7280",
                font=("Segoe UI", 9)).pack(anchor="w")

        status_var = tk.StringVar(value="● หยุด")
        status_lbl = tk.Label(left, textvariable=status_var, bg="#151821",
                              fg="#f87171", font=("Segoe UI", 9, "bold"))
        status_lbl.pack(anchor="w")

        right = tk.Frame(inner, bg="#151821")
        right.pack(side="right")

        btn_start = tk.Button(right, text="▶ เริ่ม", bg="#166534", fg="white",
                             font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                             cursor="hand2", command=start_cmd)
        btn_start.pack(side="left", padx=3)

        btn_stop = tk.Button(right, text="⏹ หยุด", bg="#374151", fg="#9ca3af",
                            font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                            cursor="hand2", command=stop_cmd)
        btn_stop.pack(side="left", padx=3)

        btn_open = tk.Button(right, text="🔗 เปิด", bg="#1e3a5f", fg="#60a5fa",
                            font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                            cursor="hand2", command=open_cmd)
        btn_open.pack(side="left", padx=3)

        return {"status_var": status_var, "status_lbl": status_lbl,
                "desc_var": desc_var, "btn_start": btn_start, "btn_stop": btn_stop}

    # ── Log ────────────────────────────────────────────────────────────────
    def log(self, msg, tag=None):
        def _do():
            self.log_box.config(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_box.insert("end", f"[{ts}] ", "muted")
            self.log_box.insert("end", msg + "\n", tag)
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.root.after(0, _do)

    def clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    def _set_status(self, card, running: bool, extra=""):
        def _do():
            if running:
                card["status_var"].set(f"● กำลังรัน {extra}")
                card["status_lbl"].config(fg="#4ade80")
                card["btn_start"].config(bg="#374151", fg="#6b7280")
                card["btn_stop"].config(bg="#7f1d1d", fg="white")
            else:
                card["status_var"].set("● หยุด")
                card["status_lbl"].config(fg="#f87171")
                card["btn_start"].config(bg="#166534", fg="white")
                card["btn_stop"].config(bg="#374151", fg="#9ca3af")
        self.root.after(0, _do)

    # ── Stream output ──────────────────────────────────────────────────────
    def _stream(self, proc, prefix, tag):
        for line in iter(proc.stdout.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self.log(f"[{prefix}] {text}", tag)
        for line in iter(proc.stderr.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip()
            if text and "WARNING" not in text:
                self.log(f"[{prefix}] {text}", "red" if "error" in text.lower() else tag)

    # ── Web Chat ───────────────────────────────────────────────────────────
    def start_web(self):
        if self.proc_web and self.proc_web.poll() is None:
            self.log("Web Chat รันอยู่แล้ว", "yellow"); return
        self.log("▶ เริ่ม Web Chat...", "cyan")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        self.proc_web = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "webchat.py")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=BASE_DIR, env=env,
        )
        threading.Thread(target=self._stream, args=(self.proc_web, "WEB", "cyan"), daemon=True).start()
        self._set_status(self._web_card, True, f"port {WEB_PORT}")
        self.log(f"✓ Web Chat → http://localhost:{WEB_PORT}", "green")

    def stop_web(self):
        if self.proc_web:
            self.proc_web.terminate()
            self.proc_web = None
        self._set_status(self._web_card, False)
        self.log("⏹ หยุด Web Chat", "yellow")

    # ── Line Bot ───────────────────────────────────────────────────────────
    def start_line(self):
        token  = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
        secret = os.getenv("LINE_CHANNEL_SECRET", "")
        if not token or token == "your_channel_access_token_here" or not secret:
            messagebox.showwarning("ตั้งค่าไม่ครบ",
                "กรุณาตั้งค่า LINE_CHANNEL_ACCESS_TOKEN\nและ LINE_CHANNEL_SECRET ใน .env ก่อน")
            return
        if self.proc_line and self.proc_line.poll() is None:
            self.log("Line Bot รันอยู่แล้ว", "yellow"); return
        self.log("▶ เริ่ม Line Bot...", "blue")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        self.proc_line = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "main.py")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=BASE_DIR, env=env,
        )
        threading.Thread(target=self._stream, args=(self.proc_line, "LINE", "blue"), daemon=True).start()
        self._set_status(self._line_card, True, "port 8000")
        self.log("✓ Line Bot → http://localhost:8000", "green")

    def stop_line(self):
        if self.proc_line:
            self.proc_line.terminate()
            self.proc_line = None
        self._set_status(self._line_card, False)
        self.log("⏹ หยุด Line Bot", "yellow")

    # ── ngrok ──────────────────────────────────────────────────────────────
    def start_ngrok(self):
        if self.proc_ngrok and self.proc_ngrok.poll() is None:
            self.log("ngrok รันอยู่แล้ว", "yellow"); return

        # หา ngrok
        ngrok_path = None
        for candidate in ["ngrok.exe", "ngrok",
                           os.path.join(BASE_DIR, "ngrok.exe"),
                           os.path.join(BASE_DIR, "ngrok")]:
            if os.path.exists(candidate) or self._in_path(candidate):
                ngrok_path = candidate
                break

        if not ngrok_path:
            messagebox.showwarning("ไม่พบ ngrok",
                "ไม่พบ ngrok.exe\nวาง ngrok.exe ไว้ในโฟลเดอร์เดียวกันหรือ PATH")
            return

        domain = os.getenv("NGROK_DOMAIN", "")
        cmd = [ngrok_path, "http"]
        if domain:
            cmd += ["--domain=" + domain]
        cmd.append("8000")

        self.log(f"▶ เริ่ม ngrok... {' '.join(cmd)}", "yellow")
        self.proc_ngrok = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=BASE_DIR,
        )
        threading.Thread(target=self._stream, args=(self.proc_ngrok, "NGROK", "yellow"), daemon=True).start()
        self._set_status(self._ngrok_card, True, "→ port 8000")
        self.log("✓ ngrok UI → http://localhost:4040", "green")

    def stop_ngrok(self):
        if self.proc_ngrok:
            self.proc_ngrok.terminate()
            self.proc_ngrok = None
        self._set_status(self._ngrok_card, False)
        self.log("⏹ หยุด ngrok", "yellow")

    def _in_path(self, cmd):
        import shutil
        return shutil.which(cmd) is not None

    # ── All ────────────────────────────────────────────────────────────────
    def start_all(self):
        self.start_web()
        time.sleep(0.5)
        self.start_line()
        time.sleep(0.5)
        self.start_ngrok()

    def stop_all(self):
        self.stop_web()
        self.stop_line()
        self.stop_ngrok()
        self.log("⏹ หยุดทุก server", "yellow")

    def on_close(self):
        if messagebox.askokcancel("ปิดโปรแกรม", "ต้องการปิด Launcher?\n(Server ทั้งหมดจะหยุดทำงาน)"):
            self.stop_all()
            self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app  = GemmaLauncher(root)
    root.mainloop()