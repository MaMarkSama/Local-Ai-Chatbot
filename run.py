"""
run.py — รัน Line Bot และ Web Chat พร้อมกันใน process เดียว
"""
import subprocess
import sys
import os
import signal
import threading
import time
from dotenv import load_dotenv

load_dotenv()

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

processes = []


def stream_output(proc, prefix, color):
    for line in iter(proc.stdout.readline, b""):
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            print(f"{color}[{prefix}]{RESET} {text}")
    for line in iter(proc.stderr.readline, b""):
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            print(f"{color}[{prefix}]{RESET} {RED}{text}{RESET}")


def start_process(name, command, color):
    proc = subprocess.Popen(
        [sys.executable] + command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    processes.append(proc)
    t = threading.Thread(target=stream_output, args=(proc, name, color), daemon=True)
    t.start()
    return proc


def shutdown(sig=None, frame=None):
    print(f"\n{YELLOW}กำลังปิด servers...{RESET}")
    for p in processes:
        try: p.terminate()
        except: pass
    time.sleep(1)
    for p in processes:
        try: p.kill()
        except: pass
    print(f"{GREEN}ปิดเรียบร้อยแล้ว{RESET}")
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    base = os.path.dirname(os.path.abspath(__file__))
    has_main    = os.path.exists(os.path.join(base, "main.py"))
    has_webchat = os.path.exists(os.path.join(base, "webchat.py"))

    print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════╗
║         Gemma AI — Launcher              ║
╚══════════════════════════════════════════╝{RESET}
""")

    started = []

    if has_webchat:
        port = os.getenv("WEB_PORT", "8001")
        print(f"{GREEN}▶ Web Chat{RESET}  → http://localhost:{port}")
        start_process("WEBCHAT", ["webchat.py"], CYAN)
        started.append("webchat")
        time.sleep(2)

    if has_main:
        token  = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
        secret = os.getenv("LINE_CHANNEL_SECRET", "")
        if not token or token == "your_channel_access_token_here" or not secret:
            print(f"{YELLOW}⚠ ข้าม Line Bot{RESET}  (ยังไม่ได้ตั้งค่า .env)")
        else:
            print(f"{GREEN}▶ Line Bot{RESET}   → http://localhost:8000")
            start_process("LINEBOT", ["main.py"], BLUE)
            started.append("linebot")
            time.sleep(2)

    if not started:
        print(f"{RED}ไม่มี server ที่รันได้{RESET}")
        sys.exit(1)

    print(f"""
{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
  {GREEN}✓ ทุก server รันแล้ว{RESET}  กด Ctrl+C เพื่อปิด
{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
""")

    while True:
        time.sleep(1)
        if any(p.poll() is not None for p in processes):
            print(f"{RED}Server หยุดทำงาน{RESET}")
            shutdown()


if __name__ == "__main__":
    main()
