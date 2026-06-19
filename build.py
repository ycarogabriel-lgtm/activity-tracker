"""
Build do Activity Tracker — gera executável standalone para o SO atual.

Uso:
    python3 build.py

Saída:
    Windows → dist/ActivityTracker.exe
    macOS   → dist/ActivityTracker
"""

import subprocess
import sys
from pathlib import Path

NAME = "ActivityTracker"
DIST = Path("dist")
BUILD = Path("build")

DEPS = ["pyinstaller", "pywebview", "psutil", "Pillow"]


def ensure_deps():
    for pkg in DEPS:
        mod = pkg.lower().replace("-", "_")
        try:
            __import__(mod)
        except ImportError:
            print(f"[INFO] Instalando {pkg}...")
            subprocess.run([sys.executable, "-m", "pip", "install", pkg], check=True)
    print("[OK] Dependências prontas.")


def make_icon():
    """Gera o ícone nativo a partir do Tracker-logo.svg."""
    import make_icon as mi
    mi.main()
    if sys.platform == "darwin" and Path("icon.icns").exists():
        return "icon.icns"
    if sys.platform == "win32" and Path("icon.ico").exists():
        return "icon.ico"
    return None


def build():
    ensure_deps()

    icon_path = make_icon()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", NAME,
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--collect-all", "webview",
        "--hidden-import", "tracker",
        "--hidden-import", "server",
        "--hidden-import", "reminder",
        "--hidden-import", "psutil",
    ]

    if icon_path:
        cmd += ["--icon", icon_path]

    if sys.platform == "win32":
        cmd += [
            "--hidden-import", "win32gui",
            "--hidden-import", "win32process",
            "--hidden-import", "win32api",
            "--noconsole",
        ]
    elif sys.platform == "darwin":
        cmd += [
            "--hidden-import", "objc",
            "--hidden-import", "Foundation",
            "--hidden-import", "WebKit",
        ]

    cmd.append("main.py")

    print(f"\n[INFO] Compilando para {sys.platform} ...")
    print("[INFO] Isso pode levar alguns minutos.\n")

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("\n[ERRO] Build falhou. Verifique as mensagens acima.")
        sys.exit(1)

    out = DIST / (f"{NAME}.exe" if sys.platform == "win32" else NAME)

    print(f"\n{'='*60}")
    print(f"  Build concluído!")
    print(f"  Executável: {out.resolve()}")
    if sys.platform == "darwin":
        print(f"\n  Para rodar:")
        print(f'    chmod +x "{out.resolve()}"')
        print(f'    "{out.resolve()}"')
    print(f"{'='*60}")


if __name__ == "__main__":
    build()
