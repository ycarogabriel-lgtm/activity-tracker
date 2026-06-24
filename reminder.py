"""
Lembretes de apontamento de horas — Activity Tracker.

Regras ativas:
  1. Quarta-feira às 17:00 → lembrete semanal de meio-semana
  2. Dia 27 de cada mês   → alerta de fechamento de folha (às 09:00)
  3. Sexta-feira às 13:30 → lembrete de consolidar horas da semana
"""

import sys
import time
import threading
import subprocess
from datetime import datetime

IS_MACOS   = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

# ─── Intervalo de verificação ─────────────────────────────────────────────────
CHECK_INTERVAL = 30   # segundos

# ─── Definição dos lembretes ──────────────────────────────────────────────────
REMINDERS = [
    {
        "id": "quarta_1700",
        "title": "⏰ Lembrete: Apontamento de Horas",
        "message": (
            "Hoje já é quarta-feira! Não deixe seu apontamento "
            "para a última hora. 📋"
        ),
        "match": lambda dt: dt.weekday() == 2 and dt.hour == 17 and dt.minute == 0,
    },
    {
        "id": "dia27_folha",
        "title": "📅 Fechamento de Folha se Aproximando",
        "message": (
            "O fechamento de folha está chegando! "
            "Certifique-se de que seu apontamento de horas está em dia. 🗂️"
        ),
        "match": lambda dt: dt.day == 27 and dt.hour == 9 and dt.minute == 0,
    },
    {
        "id": "sexta_1330",
        "title": "⏰ Lembrete: Horas da Semana",
        "message": (
            "Hoje é sexta-feira! Aproveite para consolidar e apontar "
            "todas as horas trabalhadas esta semana. 📋"
        ),
        "match": lambda dt: dt.weekday() == 4 and dt.hour == 13 and dt.minute == 30,
    },
]


# ─── Métodos de notificação (tentados em cascata) ─────────────────────────────

def _try_macos_notification(title: str, message: str) -> bool:
    """macOS: notificação nativa via osascript (sem dependências extras)."""
    def _clean(s: str) -> str:
        return s.encode("ascii", "ignore").decode().replace('"', '\\"')
    try:
        result = subprocess.run(
            ["osascript", "-e",
             f'display notification "{_clean(message)}" with title "{_clean(title)}"'],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[{_now()}] [LEMBRETE] osascript falhou: {e}")
        return False


def _try_winotify(title: str, message: str) -> bool:
    """Método 1 (Windows): winotify — mais confiável, instala automaticamente se ausente."""
    try:
        import importlib
        import importlib.util
        if importlib.util.find_spec("winotify") is None:
            if getattr(sys, "frozen", False):
                # No executável compilado, sys.executable é o próprio app, não um
                # interpretador Python — rodar "-m pip install" aqui relançaria o
                # próprio ActivityTracker.exe (sem o flag --daemon), abrindo uma
                # janela nova a cada lembrete. Pula direto para o fallback.
                return False
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "winotify", "--quiet"],
                capture_output=True, timeout=30,
            )
        from winotify import Notification, audio
        toast = Notification(
            app_id="Activity Tracker",
            title=title,
            msg=message,
            duration="long",
        )
        toast.set_audio(audio.Reminder, loop=False)
        toast.show()
        return True
    except Exception as e:
        print(f"[{_now()}] [LEMBRETE] winotify falhou: {e}")
        return False


def _try_powershell(title: str, message: str) -> bool:
    """
    Método 2: PowerShell usando o AppID do próprio PowerShell,
    que já está registrado no Windows.
    """
    def _esc(s):
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;")
                 # Remove emojis para evitar problemas de encoding no XML
                 .encode("ascii", "ignore").decode())

    ps_script = f"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' }})[0]
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$template = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{_esc(title)}</text>
      <text>{_esc(message)}</text>
    </binding>
  </visual>
</toast>
"@

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe')
$notifier.Show($toast)
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, timeout=15,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[{_now()}] [LEMBRETE] PowerShell toast falhou: {e}")
        return False


def _try_msgbox(title: str, message: str) -> bool:
    """
    Método 3: fallback com VBScript — abre uma caixa de diálogo simples.
    Sempre funciona no Windows, mas requer clique do usuário para fechar.
    """
    # Remove emojis para o VBScript
    clean_title   = title.encode("ascii", "ignore").decode()
    clean_message = message.encode("ascii", "ignore").decode()
    vbs = (
        f'MsgBox "{clean_message}", 64, "{clean_title}"'
    )
    try:
        subprocess.Popen(["wscript.exe", "//B", "//E:vbscript", "-e", vbs])
        return True
    except Exception:
        # Último recurso: msg.exe no console
        try:
            subprocess.run(
                ["msg", "*", f"{clean_title}\n{clean_message}"],
                capture_output=True, timeout=5,
            )
            return True
        except Exception as e:
            print(f"[{_now()}] [LEMBRETE] Fallback msgbox falhou: {e}")
            return False


def send_toast(title: str, message: str):
    """Tenta enviar notificação pelos métodos disponíveis, em ordem de preferência."""
    if IS_MACOS:
        if _try_macos_notification(title, message):
            print(f"[{_now()}] [LEMBRETE] ✓ Notificação enviada (macOS): {title}")
            return
        print(f"[{_now()}] [LEMBRETE] ✗ Não foi possível exibir notificação: {title}")
        return
    # Windows
    if _try_winotify(title, message):
        print(f"[{_now()}] [LEMBRETE] ✓ Notificação enviada (winotify): {title}")
        return
    if _try_powershell(title, message):
        print(f"[{_now()}] [LEMBRETE] ✓ Notificação enviada (PowerShell): {title}")
        return
    if _try_msgbox(title, message):
        print(f"[{_now()}] [LEMBRETE] ✓ Notificação enviada (msgbox): {title}")
        return
    print(f"[{_now()}] [LEMBRETE] ✗ Não foi possível exibir notificação: {title}")


# ─── Loop principal ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def reminder_loop():
    print(f"[{_now()}] [LEMBRETE] Monitorando {len(REMINDERS)} lembrete(s):")
    for r in REMINDERS:
        print(f"           • {r['id']}")

    fired_today: set = set()

    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            for reminder in REMINDERS:
                fire_key = f"{reminder['id']}:{today_str}"
                if fire_key not in fired_today and reminder["match"](now):
                    send_toast(reminder["title"], reminder["message"])
                    fired_today.add(fire_key)

            fired_today = {k for k in fired_today if k.endswith(today_str)}

        except Exception as e:
            print(f"[{_now()}] [LEMBRETE] Erro no loop: {e}")

        time.sleep(CHECK_INTERVAL)


# ─── Entrypoint ───────────────────────────────────────────────────────────────

def start_reminder_thread() -> threading.Thread:
    t = threading.Thread(target=reminder_loop, name="ReminderThread", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    if "--test" in sys.argv:
        print("[LEMBRETE] Modo de teste: disparando todas as notificações...")
        for r in REMINDERS:
            print(f"  → {r['id']}")
            send_toast(r["title"], r["message"])
            time.sleep(3)
        print("[LEMBRETE] Concluído.")
    else:
        try:
            reminder_loop()
        except KeyboardInterrupt:
            print("\n[LEMBRETE] Encerrado pelo usuário.")
