"""
Inicializador do Activity Tracker.
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

SCRIPT_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent

# ─── Autostart / background mode ──────────────────────────────────────────────

_PLIST_LABEL       = "com.activitytracker"
_PLIST_PATH        = Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"
_LOGIN_PLIST_LABEL = "com.activitytracker.login"
_LOGIN_PLIST_PATH  = Path.home() / "Library" / "LaunchAgents" / f"{_LOGIN_PLIST_LABEL}.plist"
_REG_KEY           = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_NAME          = "ActivityTracker-Daemon"
_LOGIN_REG_NAME    = "ActivityTracker"


def _background_enabled() -> bool:
    if sys.platform == "darwin":
        return _PLIST_PATH.exists()
    if sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(k, _REG_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(k)
        except Exception:
            return False
    return False


def _enable_background():
    exe = sys.executable  # path do .app ou .exe
    if sys.platform == "darwin":
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>--daemon</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/tmp/activity-tracker-daemon.log</string>
    <key>StandardErrorPath</key><string>/tmp/activity-tracker-daemon-error.log</string>
</dict>
</plist>"""
        _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PLIST_PATH.write_text(plist)
        subprocess.run(["launchctl", "load", str(_PLIST_PATH)], capture_output=True)
    elif sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(k, _REG_NAME, 0, winreg.REG_SZ, f'"{exe}" --daemon')
            winreg.CloseKey(k)
        except Exception as e:
            print(f"[AVISO] Erro ao habilitar autostart: {e}")


def _disable_background():
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], capture_output=True)
        _PLIST_PATH.unlink(missing_ok=True)
    elif sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(k, _REG_NAME)
            except FileNotFoundError:
                pass
            winreg.CloseKey(k)
        except Exception as e:
            print(f"[AVISO] Erro ao desabilitar autostart: {e}")


def _login_enabled() -> bool:
    if sys.platform == "darwin":
        return _LOGIN_PLIST_PATH.exists()
    if sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(k, _LOGIN_REG_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(k)
        except Exception:
            return False
    return False


def _enable_login():
    exe = sys.executable
    if sys.platform == "darwin":
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{_LOGIN_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
</dict>
</plist>"""
        _LOGIN_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LOGIN_PLIST_PATH.write_text(plist)
        subprocess.run(["launchctl", "load", str(_LOGIN_PLIST_PATH)], capture_output=True)
    elif sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(k, _LOGIN_REG_NAME, 0, winreg.REG_SZ, f'"{exe}"')
            winreg.CloseKey(k)
        except Exception as e:
            print(f"[AVISO] Erro ao habilitar iniciar no login: {e}")


def _disable_login():
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "unload", str(_LOGIN_PLIST_PATH)], capture_output=True)
        _LOGIN_PLIST_PATH.unlink(missing_ok=True)
    elif sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(k, _LOGIN_REG_NAME)
            except FileNotFoundError:
                pass
            winreg.CloseKey(k)
        except Exception as e:
            print(f"[AVISO] Erro ao desabilitar iniciar no login: {e}")


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
        import webview
        from server import export_csv as _export_csv
        csv_text = _export_csv(date_filter)
        fname = f"atividades_{date_filter or 'todas'}.csv"

        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=fname,
            file_types=("Arquivo CSV (*.csv)",),
        )
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(csv_text)
        return path

    def get_settings(self):
        from server import LOG_FILE
        return {
            "background_mode": _background_enabled(),
            "login_mode": _login_enabled(),
            "data_dir": str(LOG_FILE),
            "platform": sys.platform,
        }

    def save_setting(self, key, value):
        if key == "background_mode":
            if value:
                _enable_background()
            else:
                _disable_background()
        elif key == "login_mode":
            if value:
                _enable_login()
            else:
                _disable_login()
        return True


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
        text_select=True,            # permite selecionar/copiar texto na janela
    )
    webview.start()

    print("[INFO] Encerrado.")


if __name__ == "__main__":
    main()
