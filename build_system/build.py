# -*- coding: utf-8 -*-
"""
build.py — Build GemmaAI_Setup.exe
แก้ไขค่าใน BUILD_CONFIG เพื่อปรับแต่ง

โครงสร้างโฟลเดอร์ที่ถูกต้อง:
  final_package/
  ├── build_system/
  │   ├── build.bat
  │   ├── build.py        ← ไฟล์นี้
  │   └── README_BUILD.md
  ├── main.py
  ├── setup_wizard.py
  └── ...ไฟล์โปรแกรมอื่น ๆ
"""
import os, sys, shutil, subprocess, zipfile
from pathlib import Path

# ══════════════════════════════════════════════════════════════
#  BUILD CONFIG — แก้ตรงนี้เพื่อปรับแต่ง
# ══════════════════════════════════════════════════════════════
BUILD_CONFIG = {
    # ── ข้อมูลแอป ──────────────────────────────────────────
    "app_name":    "GemmaAI_Setup",
    "app_version": "1.0.1",
    "app_author":  "MaMarkSama",
    "app_desc":    "Gemma AI Local Chatbot Installer",

    # ── ตำแหน่งไฟล์ ────────────────────────────────────────
    # source_dir = โฟลเดอร์ที่มีไฟล์โปรแกรมทั้งหมด
    # ".." หมายถึงโฟลเดอร์แม่ของ build_system/ (คือ final_package/)
    "source_dir":  "..",

    # ── ไฟล์ที่จะ bundle เข้าไปใน .exe ──────────────────────
    # sync กับ _copy_files_to_install_dir() ใน setup_wizard.py
    "bundle_files": [
        "main.py",
        "webchat.py",
        "file_processor.py",
        "conversation.py",
        "database.py",
        "launcher.py",
        "run.py",
        "requirements.txt",
        ".env.example",
        "start.bat",
        "install.bat",
        "setup_wizard.py",
        "security.py",
        "auth_middleware.py",
        "user_manager.py",
        "memory_manager.py",
        "message_turbovec.py",
        "admin_tools.py",
        "webchat_ui.html",
        "README.md",
    ],

    # ── PyInstaller options ────────────────────────────────
    "onefile":   True,     # รวมเป็นไฟล์เดียว
    "windowed":  True,     # ไม่แสดง terminal (GUI only)
    "icon":      None,     # path ไปยัง .ico file หรือ None

    # ── Output ────────────────────────────────────────────
    "output_dir": "dist",
}
# ══════════════════════════════════════════════════════════════

BASE = Path(__file__).parent.resolve()          # .../final_package/build_system/
SRC  = (BASE / BUILD_CONFIG["source_dir"]).resolve()  # .../final_package/
DIST = BASE / BUILD_CONFIG["output_dir"]        # .../final_package/build_system/dist/


def log(msg, color=""):
    colors = {
        "green":  "\033[92m",
        "yellow": "\033[93m",
        "red":    "\033[91m",
        "cyan":   "\033[96m",
        "":       "",
    }
    reset = "\033[0m"
    print(f"{colors.get(color,'')}{msg}{reset}")


def check_source_dir():
    """ตรวจสอบว่า source_dir มีไฟล์สำคัญอยู่จริง"""
    if not SRC.exists():
        log(f"\n✗ ไม่พบ source_dir: {SRC}", "red")
        log(f"  build_system/ ต้องอยู่ภายใน final_package/", "red")
        log(f"  โครงสร้างที่ถูกต้อง:", "yellow")
        log(f"    final_package/", "yellow")
        log(f"    ├── build_system/  ← โฟลเดอร์นี้", "yellow")
        log(f"    ├── setup_wizard.py", "yellow")
        log(f"    └── main.py  ...", "yellow")
        sys.exit(1)

    wizard = SRC / "setup_wizard.py"
    if not wizard.exists():
        log(f"\n✗ ไม่พบ setup_wizard.py ใน {SRC}", "red")
        log(f"  ตรวจสอบว่า source_dir ชี้ถูกต้องใน BUILD_CONFIG", "red")
        sys.exit(1)

    log(f"✓ source_dir: {SRC}", "green")


def create_bundle_zip():
    """สร้าง zip ของไฟล์โปรแกรมทั้งหมด เพื่อ embed ใน .exe"""
    zip_path = BASE / "bundle.zip"
    log("\nCreating bundle.zip...", "cyan")

    missing = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in BUILD_CONFIG["bundle_files"]:
            src = SRC / fname
            if src.exists():
                zf.write(src, fname)
                log(f"  + {fname} ({src.stat().st_size // 1024} KB)")
            else:
                missing.append(fname)
                log(f"  - {fname} (not found, skipping)", "yellow")

    size = zip_path.stat().st_size // 1024
    log(f"bundle.zip created: {size} KB", "green")

    if missing:
        log(f"\n⚠  ไม่พบ {len(missing)} ไฟล์: {', '.join(missing)}", "yellow")
        log("   ตรวจสอบว่าไฟล์เหล่านี้อยู่ใน source_dir ก่อน build", "yellow")

    return zip_path


def create_version_file():
    """สร้าง version info สำหรับ Windows .exe"""
    version = BUILD_CONFIG["app_version"].split(".")
    while len(version) < 4:
        version.append("0")

    ver_str = ", ".join(version)
    ver_content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({ver_str}),
    prodvers=({ver_str}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(u'040904B0', [
        StringStruct(u'CompanyName', u'{BUILD_CONFIG["app_author"]}'),
        StringStruct(u'FileDescription', u'{BUILD_CONFIG["app_desc"]}'),
        StringStruct(u'FileVersion', u'{BUILD_CONFIG["app_version"]}'),
        StringStruct(u'InternalName', u'{BUILD_CONFIG["app_name"]}'),
        StringStruct(u'ProductName', u'{BUILD_CONFIG["app_name"]}'),
        StringStruct(u'ProductVersion', u'{BUILD_CONFIG["app_version"]}'),
      ])
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)"""
    ver_path = BASE / "version_info.txt"
    ver_path.write_text(ver_content, encoding="utf-8")
    return ver_path


def patch_setup_wizard():
    """
    สร้าง setup_wizard_bundled.py ที่มี bundle extraction bootstrap
    และ override BASE_DIR ให้ชี้ไปที่โฟลเดอร์ที่ extract ออกมา
    """
    src_file = SRC / "setup_wizard.py"
    dst_file = BASE / "setup_wizard_bundled.py"

    content = src_file.read_text(encoding="utf-8")

    # ── bundle extraction bootstrap ─────────────────────────────────────────
    bundle_bootstrap = '''\
# -*- coding: utf-8 -*-
"""setup_wizard_bundled.py — auto-generated by build.py, do not edit"""
import sys, os, zipfile


def _get_bundle_zip():
    """หา bundle.zip จากตำแหน่งต่าง ๆ ตามลำดับ"""
    candidates = []

    # 1. PyInstaller _MEIPASS (onefile mode)
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "bundle.zip"))

    # 2. ข้าง .exe เมื่อรันจากโฟลเดอร์ (onedir mode)
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "bundle.zip"))

    # 3. ข้างสคริปต์นี้ (dev / test)
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bundle.zip"))

    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _extract_bundle_if_needed(dest_dir):
    """Extract bundle.zip ไปยัง dest_dir (ทำครั้งเดียว)"""
    marker = os.path.join(dest_dir, ".extracted")
    # Force extraction to ensure we use the latest files bundled in this .exe
    # if os.path.exists(marker): return True

    bundle_zip = _get_bundle_zip()
    if not bundle_zip:
        return False  # ไม่พบ bundle.zip (อาจรันโดยตรงไม่ใช่ผ่าน .exe)

    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(bundle_zip, "r") as zf:
        zf.extractall(dest_dir)

    with open(marker, "w") as f:
        f.write("extracted")
    return True


# ── กำหนด install source ──────────────────────────────────────────────────
_INSTALL_SOURCE = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "GemmaAI", "_src"
)
_extract_bundle_if_needed(_INSTALL_SOURCE)

# ─────────────────────────────────────────────────────────────────────────────
#  ส่วน setup_wizard.py ต้นฉบับเริ่มต้นด้านล่าง (ถูก patch โดย build.py)
# ─────────────────────────────────────────────────────────────────────────────
'''

    # ── ตัด shebang/encoding declaration ของต้นฉบับออก ─────────────────────
    lines = content.splitlines(keepends=True)
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "" or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        start = i
        break

    # เอา encoding comment และ docstring ออกจากต้นฉบับเพื่อไม่ให้ซ้ำ
    clean_lines = []
    skip_docstring = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # ข้าม encoding declaration
        if stripped.startswith("# -*- coding"):
            continue
        # ข้าม module docstring (บรรทัดแรก ๆ ที่เป็น triple-quote)
        if i < 10 and stripped.startswith('"""') and not skip_docstring:
            skip_docstring = True
            continue
        if skip_docstring:
            if stripped.endswith('"""'):
                skip_docstring = False
            continue
        clean_lines.append(line)

    clean_content = "".join(clean_lines)

    # ── patch BASE_DIR ────────────────────────────────────────────────────────
    OLD_BASE = 'BASE_DIR    = os.path.dirname(os.path.abspath(__file__))'
    NEW_BASE = 'BASE_DIR    = _INSTALL_SOURCE  # patched by build.py'

    if OLD_BASE in clean_content:
        clean_content = clean_content.replace(OLD_BASE, NEW_BASE, 1)
        log("✓ BASE_DIR patched", "green")
    else:
        # fallback: เพิ่มบรรทัด override ก่อน import แรก
        log("⚠  ไม่พบ BASE_DIR line — ใส่ override ไว้ต้นไฟล์", "yellow")
        clean_content = f"BASE_DIR = _INSTALL_SOURCE  # injected by build.py\n" + clean_content

    patched = bundle_bootstrap + clean_content
    dst_file.write_text(patched, encoding="utf-8")
    log(f"✓ setup_wizard_bundled.py created ({dst_file.stat().st_size // 1024} KB)", "green")
    return dst_file


def build_exe():
    """รัน PyInstaller"""
    cfg = BUILD_CONFIG
    log("\nRunning PyInstaller...", "cyan")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        f"--name={cfg['app_name']}",
        f"--distpath={DIST}",
        f"--workpath={BASE / 'build_tmp'}",
        f"--specpath={BASE}",
    ]

    if cfg["onefile"]:
        cmd.append("--onefile")
    if cfg["windowed"]:
        cmd.append("--windowed")
    if cfg.get("icon") and os.path.exists(cfg["icon"]):
        cmd.append(f"--icon={cfg['icon']}")

    # รวม bundle.zip เข้าไปด้วย
    bundle_zip = BASE / "bundle.zip"
    if bundle_zip.exists():
        cmd.append(f"--add-data={bundle_zip}{os.pathsep}.")

    # Version info (Windows only)
    ver_file = BASE / "version_info.txt"
    if ver_file.exists() and sys.platform == "win32":
        cmd.append(f"--version-file={ver_file}")

    # tkinter hidden imports (GUI ต้องการ)
    for mod in [
        "tkinter", "tkinter.ttk",
        "tkinter.scrolledtext", "tkinter.filedialog",
        "tkinter.messagebox",
    ]:
        cmd += [f"--hidden-import={mod}"]

    # Entry point
    cmd.append(str(BASE / "setup_wizard_bundled.py"))

    log(f"\nCommand: {' '.join(str(c) for c in cmd)}\n", "cyan")
    result = subprocess.run(cmd, cwd=BASE)
    return result.returncode == 0


def create_release_zip():
    """สร้าง release zip ที่มี .exe + README"""
    exe_path = DIST / f"{BUILD_CONFIG['app_name']}.exe"
    if not exe_path.exists():
        log("No .exe found to package", "red")
        return

    release_name = f"{BUILD_CONFIG['app_name']}_v{BUILD_CONFIG['app_version']}"
    release_zip  = DIST / f"{release_name}.zip"

    with zipfile.ZipFile(release_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe_path, exe_path.name)
        readme = SRC / "README.md"
        if readme.exists():
            zf.write(readme, "README.md")

    size_mb = release_zip.stat().st_size / (1024 * 1024)
    log(f"\nRelease: {release_zip.name} ({size_mb:.1f} MB)", "green")


def cleanup():
    """ลบไฟล์ชั่วคราว"""
    for fname in [
        "bundle.zip",
        "version_info.txt",
        "setup_wizard_bundled.py",
        f"{BUILD_CONFIG['app_name']}.spec",
    ]:
        p = BASE / fname
        if p.exists():
            p.unlink()
            log(f"  removed {fname}")

    tmp = BASE / "build_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
        log("  removed build_tmp/")


def main():
    log("=" * 56, "cyan")
    log(f"  Building {BUILD_CONFIG['app_name']} v{BUILD_CONFIG['app_version']}", "cyan")
    log(f"  Source : {SRC}", "cyan")
    log(f"  Output : {DIST}", "cyan")
    log("=" * 56, "cyan")

    # 1. ตรวจสอบ source_dir
    check_source_dir()

    # 2. สร้าง bundle.zip
    create_bundle_zip()

    # 3. สร้าง version info
    create_version_file()

    # 4. Patch setup_wizard → setup_wizard_bundled.py
    patch_setup_wizard()

    # 5. Build .exe
    ok = build_exe()

    if ok:
        exe = DIST / f"{BUILD_CONFIG['app_name']}.exe"
        size_mb = exe.stat().st_size / (1024 * 1024) if exe.exists() else 0
        log(f"\n✓ Build สำเร็จ!  {exe}  ({size_mb:.1f} MB)", "green")

        # 6. สร้าง release zip
        create_release_zip()
    else:
        log("\n✗ Build ล้มเหลว — ดู error ด้านบน", "red")
        cleanup()
        sys.exit(1)

    # 7. Cleanup
    cleanup()
    log("\nDone!", "green")


if __name__ == "__main__":
    main()