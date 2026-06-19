"""
Inicializador do Activity Tracker.
"""

import subprocess
import sys
import threading
from pathlib import Path

SCRIPT_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


def check_dependencies():
    if getattr(sys, "frozen", False):
        return

    missing = []
    if sys.platform == "win32":
        try:
            import win32gui  # noqa: F401
        except ImportError:
            missing.append("pywin32")
    try:
        import psutil  # noqa: F401
    except ImportError:
        missing.append("psutil")
    try:
        import webview  # noqa: F401
    except ImportError:
        missing.append("pywebview")

    if missing:
        print(f"[INFO] Instalando dependências: {', '.join(missing)}")
        subprocess.run([sys.executable, "-m", "pip", "install"] + missing, check=True)
        print("[INFO] Dependências instaladas.")


class AppApi:
    """API Python exposta ao JavaScript via pywebview."""

    def get_data(self):
        from server import get_api_data
        return get_api_data()

    def export_csv(self, date_filter=None):
        from server import export_csv
        return export_csv(date_filter)


def main():
    print("=" * 60)
    print("  Activity Tracker - Iniciando...")
    print("=" * 60)

    check_dependencies()

    # Tracker em thread daemon
    from tracker import main as tracker_main
    threading.Thread(target=tracker_main, daemon=True, name="TrackerThread").start()
    print("[OK] Rastreador iniciado")

    # Lembretes em thread daemon
    from reminder import start_reminder_thread
    start_reminder_thread()
    print("[OK] Lembretes ativados")

    # Servidor HTTP em background (para acesso via navegador externo)
    from server import main as server_main, HTML_TEMPLATE
    threading.Thread(target=server_main, daemon=True, name="ServerThread").start()

    print("[OK] Abrindo Activity Tracker...")

    import webview
    webview.create_window(
        "Activity Tracker",
        html=HTML_TEMPLATE,          # carrega o HTML diretamente — sem depender de HTTP
        js_api=AppApi(),             # expõe get_data() e export_csv() ao JavaScript
        width=1300,
        height=820,
        min_size=(900, 600),
    )
    webview.start()

    print("[INFO] Encerrado.")


if __name__ == "__main__":
    main()
