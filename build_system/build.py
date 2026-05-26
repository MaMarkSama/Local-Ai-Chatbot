# -*- coding: utf-8 -*-
"""
build.py — Build GemmaAI_Setup.exe
แก้ไขค่าใน BUILD_CONFIG เพื่อปรับแต่ง
"""
import os, sys, shutil, subprocess, zipfile, json
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

    # ── ไฟล์ที่จะรวมใน installer ──────────────────────────
    "source_dir":  "..",          # โฟลเดอร์ของโปรแกรมหลัก (relative จาก build_system/)
    "entry_point": "../setup_wizard.py",   # ไฟล์หลักที่รัน

    # ไฟล์ที่จะ bundle เข้าไปใน .exe (จะ extract ตอนติดตั้ง)
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
    ],

    # ── PyInstaller options ────────────────────────────────
    "onefile":   True,     # รวมเป็นไฟล์เดียว
    "windowed":  True,     # ไม่แสดง terminal (GUI only)
    "icon":      None,     # path ไปยัง .ico file หรือ None

    # ── Output ────────────────────────────────────────────
    "output_dir": "dist",
}
# ══════════════════════════════════════════════════════════════

BASE = Path(__file__).parent
SRC  = (BASE / BUILD_CONFIG["source_dir"]).resolve()
DIST = BASE / BUILD_CONFIG["output_dir"]


def log(msg, color=""):
    colors = {"green":"\033[92m","yellow":"\033[93m","red":"\033[91m","cyan":"\033[96m","":""}
    reset  = "\033[0m"
    print(f"{colors.get(color,'')}{msg}{reset}")


def create_bundle_zip():
    """สร้าง zip ของไฟล์โปรแกรมทั้งหมด เพื่อ embed ใน .exe"""
    zip_path = BASE / "bundle.zip"
    log(f"Creating bundle.zip...", "cyan")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in BUILD_CONFIG["bundle_files"]:
            src = SRC / fname
            if src.exists():
                zf.write(src, fname)
                log(f"  + {fname} ({src.stat().st_size // 1024} KB)")
            else:
                log(f"  - {fname} (not found, skipping)", "yellow")

    size = zip_path.stat().st_size // 1024
    log(f"bundle.zip created: {size} KB", "green")
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
    """แก้ setup_wizard.py ให้รู้จัก bundle.zip เมื่อรันจาก .exe"""
    src_file = SRC / "setup_wizard.py"
    dst_file = BASE / "setup_wizard_bundled.py"

    content = src_file.read_text(encoding="utf-8")

    # เพิ่ม bundle extraction logic ที่ต้นไฟล์
    bundle_code = '''# -*- coding: utf-8 -*-
import sys, os, zipfile, tempfile, shutil

def _get_bundle_dir():
    """หา directory ที่มี bundle.zip"""
    if getattr(sys, 'frozen', False):
        # รันจาก .exe (PyInstaller)
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _extract_bundle_if_needed(dest_dir):
    """Extract bundle.zip ไปยัง dest_dir ถ้ายังไม่มีไฟล์"""
    bundle_zip = os.path.join(_get_bundle_dir(), "bundle.zip")
    marker     = os.path.join(dest_dir, ".extracted")

    if os.path.exists(marker):
        return True  # extract แล้ว

    if not os.path.exists(bundle_zip):
        # ถ้าไม่มี bundle.zip ให้หาจาก _MEIPASS (PyInstaller temp)
        if hasattr(sys, '_MEIPASS'):
            bundle_zip = os.path.join(sys._MEIPASS, "bundle.zip")

    if not os.path.exists(bundle_zip):
        return False

    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(bundle_zip, "r") as zf:
        zf.extractall(dest_dir)

    # เขียน marker
    open(marker, "w").write("extracted")
    return True

# Override BASE_DIR ให้ชี้ไปที่โฟลเดอร์ที่ extract ออกมา
_INSTALL_SOURCE = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "GemmaAI", "_src"
)
_extract_bundle_if_needed(_INSTALL_SOURCE)

'''
    # แทนที่ BASE_DIR definition
    patched = bundle_code + content.replace(
        'BASE_DIR    = os.path.dirname(os.path.abspath(__file__))',
        f'BASE_DIR    = _INSTALL_SOURCE  # patched by build.py'
    )

    dst_file.write_text(patched, encoding="utf-8")
    log("setup_wizard_bundled.py created", "green")
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
        cmd.append(f"--add-data={bundle_zip};.")

    # Version info (Windows only)
    ver_file = BASE / "version_info.txt"
    if ver_file.exists() and sys.platform == "win32":
        cmd.append(f"--version-file={ver_file}")

    # Entry point
    cmd.append(str(BASE / "setup_wizard_bundled.py"))

    result = subprocess.run(cmd, cwd=BASE)
    return result.returncode == 0


def create_release_zip():
    """สร้าง release zip ที่มีทุกอย่าง"""
    exe_path  = DIST / f"{BUILD_CONFIG['app_name']}.exe"
    if not exe_path.exists():
        log("No .exe found to package", "red")
        return

    release_name = f"{BUILD_CONFIG['app_name']}_v{BUILD_CONFIG['app_version']}"
    release_zip  = DIST / f"{release_name}.zip"

    with zipfile.ZipFile(release_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe_path, exe_path.name)
        # เพิ่ม README ถ้ามี
        readme = SRC / "README.md"
        if readme.exists():
            zf.write(readme, "README.md")

    size_mb = release_zip.stat().st_size / (1024*1024)
    log(f"\nRelease: {release_zip.name} ({size_mb:.1f} MB)", "green")


def cleanup():
    """ลบไฟล์ชั่วคราว"""
    for f in ["bundle.zip", "version_info.txt", "setup_wizard_bundled.py",
              f"{BUILD_CONFIG['app_name']}.spec"]:
        p = BASE / f
        if p.exists():
            p.unlink()
    tmp = BASE / "build_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)


def main():
    log("=" * 50, "cyan")
    log(f"  Building {BUILD_CONFIG['app_name']} v{BUILD_CONFIG['app_version']}", "cyan")
    log("=" * 50, "cyan")

    # 1. สร้าง bundle.zip
    create_bundle_zip()

    # 2. สร้าง version info
    create_version_file()

    # 3. Patch setup_wizard
    patch_setup_wizard()

    # 4. Build .exe
    ok = build_exe()

    if ok:
        exe = DIST / f"{BUILD_CONFIG['app_name']}.exe"
        size_mb = exe.stat().st_size / (1024*1024) if exe.exists() else 0
        log(f"\n✓ Build สำเร็จ! {exe} ({size_mb:.1f} MB)", "green")

        # 5. สร้าง release zip
        create_release_zip()
    else:
        log("\n✗ Build ล้มเหลว", "red")
        sys.exit(1)

    # 6. Cleanup
    cleanup()
    log("\nDone!", "green")


if __name__ == "__main__":
    main()
