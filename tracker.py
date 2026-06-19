"""
Activity Tracker - Rastreador de Atividades (Windows & macOS)
Registra automaticamente: janela ativa, reuniões/chats do Teams e abas do navegador.
Salva os dados em activity_log.json para exibição no painel web.
"""

import json
import time
import os
import re
import sys
import subprocess
from datetime import datetime, date
from pathlib import Path

# ─── Detecção de plataforma ───────────────────────────────────────────────────
IS_MACOS   = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

# ─── Dependências opcionais ───────────────────────────────────────────────────
WIN32_AVAILABLE = False
if IS_WINDOWS:
    try:
        import win32gui
        import win32process
        import psutil
        WIN32_AVAILABLE = True
    except ImportError:
        print("[AVISO] pywin32/psutil não encontrados. Execute: pip install pywin32 psutil")

if IS_MACOS:
    try:
        import psutil  # noqa: F401 (opcional, não obrigatório no macOS)
    except ImportError:
        pass  # psutil não é obrigatório no macOS

# ─── Configurações ────────────────────────────────────────────────────────────
def _data_dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "ActivityTracker"
        d.mkdir(parents=True, exist_ok=True)
        return d
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent  # Windows exe
    return Path(__file__).parent  # modo desenvolvimento

SCRIPT_DIR = _data_dir()
LOG_FILE  = SCRIPT_DIR / "activity_log.json"
LOCK_FILE = SCRIPT_DIR / "tracker.lock"
INTERVAL_SECONDS = 10          # Intervalo de captura (segundos)
MAX_RECORDS = 5000             # Máximo de registros mantidos no JSON


def _pid_running(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_lock() -> bool:
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            if _pid_running(pid):
                return False
        except Exception:
            pass
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def _release_lock():
    try:
        LOCK_FILE.unlink()
    except Exception:
        pass

# Navegadores conhecidos — adaptado por plataforma (nomes em minúsculas)
if IS_MACOS:
    BROWSERS = {
        "google chrome", "safari", "firefox", "brave browser",
        "microsoft edge", "opera", "vivaldi", "arc",
    }
else:  # Windows
    BROWSERS = {
        "chrome.exe", "msedge.exe", "firefox.exe",
        "brave.exe", "opera.exe", "vivaldi.exe",
    }

# Padrões de título do Teams para detectar reuniões e chats
TEAMS_MEETING_PATTERNS = [
    r"\| Microsoft Teams$",
    r"Meeting",
    r"Reunião",
    r"Call",
    r"Lobby",
    r"Waiting",
    r"Pre-join",
]
TEAMS_CHAT_PATTERN = re.compile(r"^(.+?)\s*[|–-]\s*(?:Chat\s*[|–-]\s*)?Microsoft Teams", re.IGNORECASE)
TEAMS_MEETING_PATTERN = re.compile(r"^(.+?)\s*\|\s*Microsoft Teams$", re.IGNORECASE)

# ─── Funções auxiliares ───────────────────────────────────────────────────────

def get_active_window_info():
    """Retorna (título, nome_do_processo, categoria) da janela ativa."""
    if IS_MACOS:
        return _get_active_window_macos()
    if WIN32_AVAILABLE:
        return _get_active_window_win32()
    return _get_active_window_fallback()


def _get_active_window_macos():
    """Obtém a janela ativa no macOS via osascript (sem dependências extras)."""
    try:
        script = '''
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    set windowTitle to ""
    try
        set windowTitle to name of first window of frontApp
    end try
    return appName & "|~|" & windowTitle
end tell'''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3
        )
        output = result.stdout.strip()
        if "|~|" in output:
            parts = output.split("|~|", 1)
            proc_name = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else proc_name
        else:
            proc_name = output
            title = output
        if not title:
            title = proc_name
        category, detail = classify_window(title, proc_name)
        return {
            "title": title,
            "process": proc_name,
            "category": category,
            "detail": detail,
        }
    except Exception as e:
        return {"title": "", "process": "", "category": "idle", "detail": str(e)}


def _get_active_window_win32():
    """Obtém a janela ativa no Windows via win32gui."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc = psutil.Process(pid)
            proc_name = proc.name().lower()
            proc_display = proc.name()
        except Exception:
            proc_name = ""
            proc_display = "Desconhecido"
        category, detail = classify_window(title, proc_name)
        return {
            "title": title,
            "process": proc_display,
            "category": category,
            "detail": detail,
        }
    except Exception as e:
        return {"title": "", "process": "", "category": "idle", "detail": str(e)}


def _get_active_window_fallback():
    """Fallback usando xdotool (Linux/WSL) ou retorno vazio."""
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=2
        )
        title = result.stdout.strip()
        category, detail = classify_window(title, "")
        return {"title": title, "process": "", "category": category, "detail": detail}
    except Exception:
        return {"title": "", "process": "", "category": "idle", "detail": ""}


def classify_window(title: str, proc_name: str) -> tuple:
    """
    Classifica a janela ativa em uma categoria e extrai detalhes relevantes.
    Retorna (categoria, detalhe).
    Categorias: teams_meeting, teams_chat, browser, app, idle
    """
    title_lower = title.lower()
    proc_lower = proc_name.lower()

    # ── Teams ──────────────────────────────────────────────────────────────────
    if "teams" in proc_lower or "msteams" in proc_lower:
        # Reunião: título contém algo antes de "| Microsoft Teams"
        # Exemplos: "Daily Scrum | Microsoft Teams", "Reunião com João | Microsoft Teams"
        meeting_match = TEAMS_MEETING_PATTERN.match(title)
        if meeting_match:
            meeting_name = meeting_match.group(1).strip()
            # Exclui janelas que são apenas o app principal
            if meeting_name.lower() not in ("microsoft teams", "teams"):
                # Verifica se parece chat ou reunião
                if any(kw in title_lower for kw in ["meeting", "reunião", "call", "lobby", "waiting", "pre-join", "joining"]):
                    return "teams_meeting", meeting_name
                # Título com nome de pessoa/canal → pode ser chat ou reunião
                return "teams_meeting", meeting_name

        # Chat: detecta "Nome | Chat | Microsoft Teams" ou "Nome | Microsoft Teams"
        if "chat" in title_lower or TEAMS_CHAT_PATTERN.match(title):
            chat_match = TEAMS_CHAT_PATTERN.match(title)
            person = chat_match.group(1).strip() if chat_match else title
            return "teams_chat", person

        # App principal do Teams aberto (sem reunião/chat em foco)
        return "teams_app", "Microsoft Teams"

    # ── Navegador ──────────────────────────────────────────────────────────────
    if proc_lower in BROWSERS or any(b in proc_lower for b in BROWSERS):
        # Título do navegador geralmente é "Título da Página - Nome do Navegador"
        browser_name = _get_browser_name(proc_lower)
        page_title = title
        for suffix in [f" - {browser_name}", f" — {browser_name}", f" | {browser_name}"]:
            if page_title.endswith(suffix):
                page_title = page_title[: -len(suffix)]
                break
        return "browser", page_title

    # ── Idle / bloqueado ───────────────────────────────────────────────────────
    idle_titles = ("", "program manager", "windows default lock screen",
                   "tela de bloqueio", "loginwindow", "dock", "desktop")
    if not title or title.lower() in idle_titles:
        return "idle", ""

    # ── Aplicativo genérico ────────────────────────────────────────────────────
    return "app", title


def _get_browser_name(proc_lower: str) -> str:
    if IS_MACOS:
        mapping = {
            "google chrome": "Google Chrome",
            "safari":        "Safari",
            "firefox":       "Firefox",
            "brave browser": "Brave",
            "microsoft edge":"Microsoft Edge",
            "opera":         "Opera",
            "vivaldi":       "Vivaldi",
            "arc":           "Arc",
        }
    else:
        mapping = {
            "chrome.exe":  "Google Chrome",
            "msedge.exe":  "Microsoft Edge",
            "firefox.exe": "Mozilla Firefox",
            "brave.exe":   "Brave",
            "opera.exe":   "Opera",
            "vivaldi.exe": "Vivaldi",
        }
    for key, name in mapping.items():
        if key in proc_lower:
            return name
    return "Browser"


def read_teams_log() -> dict:
    """
    Lê o arquivo de log do Teams para detectar reuniões ativas.
    Retorna dict com 'in_meeting' (bool) e 'status' (str).
    """
    log_content = ""

    if IS_MACOS:
        home = Path.home()
        macos_log_candidates = [
            # Teams clássico (versão legada macOS)
            home / "Library" / "Application Support" / "Microsoft" / "Teams" / "logs.txt",
            # Novo Teams (sandbox)
            home / "Library" / "Containers" / "com.microsoft.teams2" / "Data" /
            "Library" / "Application Support" / "Microsoft" / "MSTeams" / "Logs",
        ]
        for candidate in macos_log_candidates:
            if candidate.is_file():
                try:
                    with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
                        log_content = f.read()[-8000:]
                    break
                except Exception:
                    pass
            elif candidate.is_dir():
                log_files = sorted(candidate.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
                for lf in log_files[:2]:
                    try:
                        with open(lf, "r", encoding="utf-8", errors="ignore") as f:
                            log_content += f.read()[-8000:]
                        break
                    except Exception:
                        pass
    else:
        # Windows — Novo Teams (MSIX)
        new_log_dir = Path(os.environ.get("LOCALAPPDATA", "")) / \
            "Packages" / "MSTeams_8wekyb3d8bbwe" / "LocalCache" / "Microsoft" / "MSTeams" / "Logs"
        # Teams clássico
        old_log_path = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Teams" / "logs.txt"

        # Tenta novo Teams primeiro
        if new_log_dir.exists():
            log_files = sorted(new_log_dir.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
            for lf in log_files[:2]:
                try:
                    with open(lf, "r", encoding="utf-8", errors="ignore") as f:
                        log_content += f.read()[-8000:]
                    break
                except Exception:
                    pass

        # Fallback para Teams clássico
        if not log_content and old_log_path.exists():
            try:
                with open(old_log_path, "r", encoding="utf-8", errors="ignore") as f:
                    log_content = f.read()[-8000:]
            except Exception:
                pass

    if not log_content:
        return {"in_meeting": False, "status": "unknown"}

    # Detecta reunião ativa pelos padrões de log
    lines = log_content.splitlines()
    last_call_start = -1
    last_call_end = -1

    for i, line in enumerate(lines):
        if any(kw in line for kw in ["eventData: s::;m::1;a::1", "callingService: joinCall", "call-connected"]):
            last_call_start = i
        if any(kw in line for kw in ["eventData: s::;m::1;a::3", "callingService: leaveCall", "call-disconnected", "call ended"]):
            last_call_end = i

    in_meeting = last_call_start > last_call_end

    # Detecta status (Available, Away, Busy, etc.)
    status = "unknown"
    for line in reversed(lines):
        m = re.search(r"StatusIndicatorStateService: Added (\w+)", line)
        if m:
            status = m.group(1)
            break

    return {"in_meeting": in_meeting, "status": status}


# ─── Gerenciamento do log JSON ────────────────────────────────────────────────

def load_log() -> list:
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_log(records: list):
    # Mantém apenas os últimos MAX_RECORDS registros
    if len(records) > MAX_RECORDS:
        records = records[-MAX_RECORDS:]
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def should_record(new_entry: dict, records: list) -> bool:
    """Evita duplicatas consecutivas: só registra se algo mudou."""
    if not records:
        return True
    last = records[-1]
    return (
        last.get("category") != new_entry.get("category") or
        last.get("detail") != new_entry.get("detail") or
        last.get("process") != new_entry.get("process")
    )


def build_entry(window_info: dict, teams_log: dict) -> dict:
    """Monta o registro de atividade."""
    now = datetime.now()
    entry = {
        "timestamp": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "category": window_info["category"],
        "detail": window_info["detail"],
        "process": window_info["process"],
        "title": window_info["title"],
        "teams_status": teams_log.get("status", ""),
        "teams_in_meeting_log": teams_log.get("in_meeting", False),
    }

    # Se o log do Teams indica reunião mas a janela não detectou, força categoria
    if teams_log.get("in_meeting") and entry["category"] not in ("teams_meeting",):
        if entry["category"] in ("teams_app", "idle", "app"):
            entry["category"] = "teams_meeting"
            entry["detail"] = entry["detail"] or "Reunião ativa (log)"

    return entry


# ─── Loop principal ───────────────────────────────────────────────────────────

def main():
    if not _acquire_lock():
        print("[TRACKER] Já está rodando em outro processo. Saindo.")
        return

    print("=" * 60)
    print("  Activity Tracker - Rastreador de Atividades")
    print("  Pressione Ctrl+C para parar.")
    print(f"  Log salvo em: {LOG_FILE}")
    print("=" * 60)

    records = load_log()
    print(f"  {len(records)} registros existentes carregados.")

    try:
        while True:
            try:
                window_info = get_active_window_info()
                teams_log = read_teams_log()
                entry = build_entry(window_info, teams_log)

                if should_record(entry, records):
                    records.append(entry)
                    save_log(records)
                    cat_label = {
                        "teams_meeting": "REUNIAO Teams",
                        "teams_chat": "CHAT Teams",
                        "teams_app": "Teams (app)",
                        "browser": "Navegador",
                        "app": "Aplicativo",
                        "idle": "Ocioso",
                    }.get(entry["category"], entry["category"])
                    print(f"[{entry['time']}] {cat_label}: {entry['detail'] or entry['title'][:60]}")

                time.sleep(INTERVAL_SECONDS)

            except KeyboardInterrupt:
                print("\n[INFO] Rastreamento encerrado pelo usuario.")
                break
            except Exception as e:
                print(f"[ERRO] {e}")
                time.sleep(INTERVAL_SECONDS)
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
