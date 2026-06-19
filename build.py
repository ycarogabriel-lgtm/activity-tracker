"""
Build do Activity Tracker — gera executável standalone para o SO atual.

Uso:
    python3 build.py

Saída:
    Windows → dist/ActivityTracker.exe
    macOS   → dist/ActivityTracker
"""

import shutil
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


def build_daemon_macos():
    """
    Compila um binário headless separado (sem GUI/bundle) para o rastreador
    em segundo plano. Necessário porque, se o daemon roda dentro do mesmo
    .app, o macOS trata o processo em segundo plano como "a instância do
    app" — ao clicar para reabrir, ele só reativa o processo sem janela
    (parece travado) em vez de abrir uma nova janela.
    """
    name = f"{NAME}Daemon"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", name,
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--hidden-import", "tracker",
        "--hidden-import", "server",
        "--hidden-import", "reminder",
        "--hidden-import", "psutil",
        "daemon.py",
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("\n[ERRO] Build do daemon falhou.")
        sys.exit(1)

    daemon_bin = DIST / name
    app_resources = DIST / f"{NAME}.app" / "Contents" / "Resources"
    app_resources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(daemon_bin, app_resources / name)
    daemon_bin.chmod(0o755)
    (app_resources / name).chmod(0o755)
    daemon_bin.unlink()


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
            "--windowed",           # cria .app bundle — abre sem terminal
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

    if sys.platform == "darwin":
        out = DIST / f"{NAME}.app"
        build_daemon_macos()
    elif sys.platform == "win32":
        out = DIST / f"{NAME}.exe"
    else:
        out = DIST / NAME

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
