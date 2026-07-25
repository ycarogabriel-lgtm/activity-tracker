"""
Inicializador do Activity Tracker.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
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

# Binário headless do daemon, fora do bundle do .app — evita que o macOS
# confunda o processo em segundo plano com a instância principal do app
# (o que travava o app ao tentar reabri-lo).
_MAC_DATA_DIR    = Path.home() / "Library" / "Application Support" / "ActivityTracker"
_MAC_DAEMON_BIN  = _MAC_DATA_DIR / "ActivityTrackerDaemon"


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
        # Copia o binário headless (empacotado em Contents/Resources) para fora
        # do .app, para que o LaunchAgent rode um processo com identidade
        # própria, nunca confundido pelo macOS com a instância principal do app.
        bundled_daemon = Path(exe).parent.parent / "Resources" / "ActivityTrackerDaemon"
        program = exe
        extra_args = ["--daemon"]
        if bundled_daemon.exists():
            _MAC_DATA_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled_daemon, _MAC_DAEMON_BIN)
            _MAC_DAEMON_BIN.chmod(0o755)
            program = str(_MAC_DAEMON_BIN)
            extra_args = []

        args_xml = "\n        ".join(f"<string>{a}</string>" for a in [program] + extra_args)
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        {args_xml}
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
        _MAC_DAEMON_BIN.unlink(missing_ok=True)
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

    def export_sessions_csv(self, date_filter=None):
        import webview
        from server import export_sessions_csv as _export
        csv_text = _export(date_filter)
        fname = f"sessoes_{date_filter or 'todas'}.csv"

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

    def get_jira_config(self):
        import jira_client
        cfg = jira_client.load_config()
        tokens = jira_client.get_tokens()
        return {
            "base_url": cfg.get("base_url", ""),
            "email": cfg.get("email", ""),
            "has_jira_token": bool(tokens.get("jira_token")),
            "has_tempo_token": bool(tokens.get("tempo_token")),
        }

    def save_jira_config(self, base_url, email, jira_token=None, tempo_token=None):
        import jira_client
        jira_client.save_config(base_url, email)
        if jira_token or tempo_token:
            jira_client.save_tokens(jira_token or None, tempo_token or None)
        return True

    def test_jira_connection(self):
        import jira_client
        try:
            return jira_client.test_connection()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def assign_jira_code(self, session_ids, code, apply_to_label=False, label_key=None):
        from server import assign_jira_code as _assign, set_jira_label_code as _set_label
        if apply_to_label and label_key:
            _set_label(label_key, code or "")
        else:
            _assign(session_ids or [], code or "")
        return True

    def delete_sessions(self, session_ids):
        from server import delete_sessions as _delete
        _delete(session_ids or [])
        return True

    def send_worklog(self, session_ids, issue_key, date_str, seconds, description=""):
        import jira_client
        from server import assign_jira_code as _assign
        try:
            jira_client.send_worklog(issue_key, date_str, seconds, description)
            _assign(session_ids or [], issue_key)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_ignored_processes(self):
        import tracker
        return sorted(tracker.get_ignored_processes())

    def save_ignored_processes(self, names):
        import tracker
        cleaned = [n.strip() for n in (names or []) if n and n.strip()]
        tracker.set_ignored_processes(cleaned)
        return True

    def uninstall(self, delete_data=False):
        """
        Remove autostart, encerra o daemon em segundo plano e apaga o app
        (e opcionalmente os dados). Como o executável não pode apagar a si
        mesmo enquanto roda, um script auxiliar é disparado em processo
        separado para fazer a limpeza logo após este processo encerrar.
        """
        try:
            _disable_background()
        except Exception as e:
            print(f"[AVISO] Erro ao desabilitar segundo plano: {e}")
        try:
            _disable_login()
        except Exception as e:
            print(f"[AVISO] Erro ao desabilitar login: {e}")

        from server import LOG_FILE
        data_dir = LOG_FILE.parent
        exe = Path(sys.executable)

        if sys.platform == "darwin":
            # exe = .../ActivityTracker.app/Contents/MacOS/ActivityTracker
            app_bundle = exe.parent.parent.parent
            lines = [
                "#!/bin/bash",
                "sleep 1",
                'pkill -f "ActivityTrackerDaemon" 2>/dev/null',
                f'rm -rf "{app_bundle}"',
            ]
            if delete_data:
                lines.append(f'rm -rf "{data_dir}"')
            lines.append('rm -f "$0"')
            script_path = Path(tempfile.gettempdir()) / "activity-tracker-uninstall.sh"
            script_path.write_text("\n".join(lines) + "\n")
            script_path.chmod(0o755)
            subprocess.Popen(
                ["/bin/bash", str(script_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        elif sys.platform == "win32":
            lines = [
                "@echo off",
                "timeout /t 2 /nobreak >nul",
                f'taskkill /F /IM "{exe.name}" >nul 2>&1',
                f'del /F /Q "{exe}"',
            ]
            if delete_data:
                lines.append(f'rmdir /S /Q "{data_dir}"')
            lines.append('del "%~f0"')
            script_path = Path(tempfile.gettempdir()) / "activity-tracker-uninstall.bat"
            script_path.write_text("\r\n".join(lines) + "\r\n")
            subprocess.Popen(
                ["cmd", "/c", str(script_path)],
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                close_fds=True,
            )

        try:
            import webview
            for w in webview.windows:
                w.destroy()
        except Exception:
            pass
        os._exit(0)


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

    # Servidor HTTP: a janela principal carrega a UI por aqui (em vez de
    # html= puro) porque o <script src="/vendor/fullcalendar.min.js"> só
    # resolve com uma origem HTTP real por trás.
    from server import start_server, HTML_TEMPLATE
    server, port = start_server()
    if server is not None:
        threading.Thread(target=server.serve_forever, daemon=True, name="ServerThread").start()

    print("[OK] Abrindo Activity Tracker...")

    import webview
    if server is not None:
        webview.create_window(
            "Activity Tracker",
            url=f"http://127.0.0.1:{port}/",
            js_api=AppApi(),
            width=1300,
            height=820,
            min_size=(900, 600),
            text_select=True,
        )
    else:
        # Fallback se nenhuma porta local ficou livre: perde o calendário
        # semanal (depende do vendor servido via HTTP), mas o resto do app
        # continua funcionando.
        webview.create_window(
            "Activity Tracker",
            html=HTML_TEMPLATE,
            js_api=AppApi(),
            width=1300,
            height=820,
            min_size=(900, 600),
            text_select=True,
        )
    webview.start()

    print("[INFO] Encerrado.")


if __name__ == "__main__":
    main()
