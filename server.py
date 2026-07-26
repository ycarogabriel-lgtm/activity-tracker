"""
Servidor web local para o painel de visualização do Activity Tracker.
Acesse http://localhost:5000 no navegador após iniciar.
"""

import json
import os
import sys
from pathlib import Path


def _atomic_write_json(path: Path, data, **json_kwargs):
    """Escreve em arquivo temporário e troca com os.replace() (atômico) —
    evita corromper o arquivo se o processo for morto no meio da escrita."""
    tmp = path.parent / f".{path.name}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, **json_kwargs)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


from datetime import datetime, timedelta
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

def _data_dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "ActivityTracker"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        d = Path(appdata) / "ActivityTracker"
    else:
        d = Path.home() / ".local" / "share" / "ActivityTracker"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _migrate_if_needed(log_file: Path):
    """Na primeira execução, copia dados de locais anteriores."""
    if log_file.exists():
        return
    for candidate in [
        Path.home() / "Downloads" / "activity_log.json",
        Path.home() / "activity_log.json",
    ]:
        if candidate.exists():
            import shutil
            try:
                shutil.copy2(candidate, log_file)
            except Exception:
                pass
            break

def _vendor_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).parent
    return base / "vendor"


VENDOR_DIR = _vendor_dir()

SCRIPT_DIR = _data_dir()
LOG_FILE = SCRIPT_DIR / "activity_log.json"
SESSIONS_FILE = SCRIPT_DIR / "activity_sessions.jsonl"
ACTIVE_SESSIONS_FILE = SCRIPT_DIR / "active_sessions.json"
JIRA_CODES_FILE = SCRIPT_DIR / "jira_codes.json"
JIRA_LABEL_CODES_FILE = SCRIPT_DIR / "jira_label_codes.json"
DELETED_SESSIONS_FILE = SCRIPT_DIR / "deleted_sessions.json"
_migrate_if_needed(LOG_FILE)
PORT = int(os.environ.get("PORT", 5000))


def load_records():
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def compute_durations(records):
    """
    Calcula a duração de cada registro com base no próximo timestamp.
    Retorna lista de registros enriquecidos com 'duration_seconds'.
    """
    enriched = []
    for i, rec in enumerate(records):
        r = dict(rec)
        if "duration_seconds" not in r or r["duration_seconds"] == 0:
            if i + 1 < len(records):
                try:
                    t1 = datetime.fromisoformat(rec["timestamp"])
                    t2 = datetime.fromisoformat(records[i + 1]["timestamp"])
                    diff = (t2 - t1).total_seconds()
                    r["duration_seconds"] = min(int(diff), 7200)
                except Exception:
                    r["duration_seconds"] = 0
            else:
                r["duration_seconds"] = 0
        enriched.append(r)
    return enriched


def group_by_date(records):
    groups = defaultdict(list)
    for r in records:
        groups[r.get("date", "")].append(r)
    return dict(sorted(groups.items(), reverse=True))


def summarize_day(day_records):
    totals = defaultdict(int)
    details_map = defaultdict(lambda: defaultdict(int))
    hourly = defaultdict(int)  # hora -> segundos ativos

    for r in day_records:
        cat = r.get("category", "app")
        dur = r.get("duration_seconds", 0)
        totals[cat] += dur
        detail = r.get("detail") or r.get("title", "")[:80]
        if detail:
            details_map[cat][detail] += dur
        # Distribuição por hora
        try:
            hour = int(r.get("time", "00:00:00").split(":")[0])
            if cat != "idle":
                hourly[hour] += dur
        except Exception:
            pass

    return {
        "totals": dict(totals),
        "details": {k: dict(v) for k, v in details_map.items()},
        "hourly": dict(hourly),
    }


def summarize_day_from_sessions(sessions: list) -> dict:
    """Mesmo formato de summarize_day(), mas calculado a partir do motor novo
    de sessões. Usado como fonte ÚNICA dos cards de resumo/gráfico por hora
    sempre que há dados de sessão para o dia — evita ter dois pipelines de
    captura (log antigo vs sessões) que podem divergir no que mostram."""
    totals = defaultdict(int)
    details_map = defaultdict(lambda: defaultdict(int))
    hourly = defaultdict(int)

    for s in sessions:
        cat = s.get("category", "app")
        dur = s.get("total_seconds", 0)
        totals[cat] += dur
        detail = s.get("detail") or s.get("process", "")
        if detail:
            details_map[cat][detail] += dur

        try:
            start = datetime.fromisoformat(s["start"])
            end = datetime.fromisoformat(s["end"])
        except Exception:
            continue
        cur = start
        while cur < end:
            hour_end = cur.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            seg_end = min(end, hour_end)
            secs = (seg_end - cur).total_seconds()
            if cat != "idle":
                hourly[cur.hour] += int(secs)
            cur = seg_end

    return {
        "totals": dict(totals),
        "details": {k: dict(v) for k, v in details_map.items()},
        "hourly": dict(hourly),
    }


def load_deleted_sessions() -> set:
    if DELETED_SESSIONS_FILE.exists():
        try:
            with open(DELETED_SESSIONS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def delete_sessions(session_ids: list):
    """'Exclui' sessões marcando o id numa lista de exclusão — o JSONL de
    sessões é append-only, então não reescrevemos ele; só filtramos na leitura."""
    deleted = load_deleted_sessions()
    deleted.update(session_ids)
    _atomic_write_json(DELETED_SESSIONS_FILE, sorted(deleted), ensure_ascii=False, indent=2)


def load_sessions() -> list:
    """Lê as sessões do motor novo: fechadas (JSONL) + as que ainda estão abertas
    (checkpoint), descontando as marcadas como excluídas."""
    sessions = []
    if SESSIONS_FILE.exists():
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        sessions.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass
    if ACTIVE_SESSIONS_FILE.exists():
        try:
            with open(ACTIVE_SESSIONS_FILE, "r", encoding="utf-8") as f:
                sessions.extend(json.load(f))
        except Exception:
            pass
    deleted = load_deleted_sessions()
    if deleted:
        sessions = [s for s in sessions if s.get("id") not in deleted]
    return sessions


def load_jira_codes() -> dict:
    if JIRA_CODES_FILE.exists():
        try:
            with open(JIRA_CODES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_jira_label_codes() -> dict:
    """Códigos 'padrão' por rótulo (processo+categoria+detalhe) — aplicados
    automaticamente a toda sessão, passada ou futura, com aquele mesmo nome."""
    if JIRA_LABEL_CODES_FILE.exists():
        try:
            with open(JIRA_LABEL_CODES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def session_label_key(s: dict) -> str:
    return f'{s.get("process","")}::{s.get("category","")}::{s.get("detail","")}'


def assign_jira_code(session_ids: list, code: str):
    """Atribui (ou remove, se code vazio) um código Jira/Tempo a uma lista de
    sessões pelo id. Guardado à parte do JSONL de sessões (que é append-only),
    num mapa pequeno id -> código, barato de reescrever."""
    codes = load_jira_codes()
    now = datetime.now().isoformat(timespec="seconds")
    for sid in session_ids:
        if code:
            codes[sid] = {"code": code, "assigned_at": now}
        else:
            codes.pop(sid, None)
    _atomic_write_json(JIRA_CODES_FILE, codes, ensure_ascii=False, indent=2)


def set_jira_label_code(label_key: str, code: str):
    """Define (ou remove) o código padrão para todas as sessões — passadas e
    futuras — que compartilham o mesmo processo+categoria+detalhe."""
    codes = load_jira_label_codes()
    now = datetime.now().isoformat(timespec="seconds")
    if code:
        codes[label_key] = {"code": code, "assigned_at": now}
    else:
        codes.pop(label_key, None)
    _atomic_write_json(JIRA_LABEL_CODES_FILE, codes, ensure_ascii=False, indent=2)


def get_sessions_by_date(date_filter=None) -> dict:
    sessions = load_sessions()
    codes = load_jira_codes()
    label_codes = load_jira_label_codes()
    for s in sessions:
        c = codes.get(s.get("id"))
        if c:
            s["jira_code"] = c["code"]
        else:
            lc = label_codes.get(session_label_key(s))
            s["jira_code"] = lc["code"] if lc else None
    if date_filter:
        sessions = [s for s in sessions if s.get("date") == date_filter]
    grouped = defaultdict(list)
    for s in sessions:
        grouped[s.get("date", "")].append(s)
    result = {}
    for d, sess_list in grouped.items():
        sess_list.sort(key=lambda s: s.get("start", ""))
        result[d] = sess_list
    return result


def get_api_data(date_filter=None):
    records = load_records()
    enriched = compute_durations(records)

    if date_filter:
        enriched = [r for r in enriched if r.get("date") == date_filter]

    grouped = group_by_date(enriched)
    sessions_by_date = get_sessions_by_date(date_filter)

    result = {}
    for d in set(grouped.keys()) | set(sessions_by_date.keys()):
        recs = grouped.get(d, [])
        day_sessions = sessions_by_date.get(d, [])
        # Fonte única de verdade: se o dia já tem dados do motor novo, o resumo
        # e o gráfico por hora vêm SÓ dele (mesmo dado do calendário). O log
        # antigo só é usado como fallback pra dias anteriores à migração.
        summary = summarize_day_from_sessions(day_sessions) if day_sessions else summarize_day(recs)
        result[d] = {
            "records": recs,
            "summary": summary,
            "sessions": day_sessions,
        }
    return result


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Activity Tracker</title>
<style>
  :root {
    --bg: #0f172a;
    --surface: #1e293b;
    --surface2: #273449;
    --border: #334155;
    --text: #e2e8f0;
    --text2: #94a3b8;
    --accent: #3b82f6;
    --meeting: #8b5cf6;
    --chat: #06b6d4;
    --browser: #10b981;
    --app: #f59e0b;
    --idle: #475569;
    --teams: #7c3aed;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; min-height: 100vh; }

  /* Header */
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; align-items: center; gap: 12px; position: sticky; top: 0; z-index: 100; flex-wrap: wrap; }
  header h1 { font-size: 1.1rem; font-weight: 700; }
  header .subtitle { color: var(--text2); font-size: 0.8rem; }
  .badge-live { background: #ef4444; color: white; font-size: 0.68rem; padding: 2px 8px; border-radius: 999px; font-weight: 700; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
  .header-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
  .btn { background: var(--surface2); border: 1px solid var(--border); color: var(--text); padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 0.82rem; text-decoration: none; display: inline-block; transition: all .15s; }
  .btn:hover { background: var(--accent); border-color: var(--accent); color: white; }
  .btn-green:hover { background: #10b981; border-color: #10b981; }
  .btn-danger { background: transparent; border: 1px solid #ef4444; color: #ef4444; width: 100%; padding: 10px; text-align: center; font-size: 0.8rem; }
  .btn-danger:hover { background: #ef4444; border-color: #ef4444; color: #fff; }
  .settings-danger { margin-top: auto; padding-top: 16px; border-top: 1px solid var(--border); }
  .btn-icon { background: var(--surface2); border: 1px solid var(--border); color: var(--text2); width: 32px; height: 32px; border-radius: 8px; cursor: pointer; font-size: 1rem; display: flex; align-items: center; justify-content: center; transition: all .15s; }
  .btn-icon:hover { color: var(--text); border-color: var(--text2); }

  /* Settings panel */
  .settings-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 500; display: flex; justify-content: flex-end; animation: fadeIn .15s; }
  .settings-overlay.hidden { display: none; }
  @keyframes fadeIn { from{opacity:0} to{opacity:1} }
  .settings-panel { background: var(--bg); width: 340px; height: 100%; overflow-y: auto; display: flex; flex-direction: column; border-left: 1px solid var(--border); }
  .settings-head { display: flex; align-items: center; justify-content: space-between; padding: 20px 24px; border-bottom: 1px solid var(--border); }
  .settings-head h2 { font-size: 1rem; font-weight: 700; margin: 0; }
  .settings-close { background: none; border: none; color: var(--text2); cursor: pointer; font-size: 1.2rem; padding: 4px 6px; border-radius: 6px; }
  .settings-close:hover { background: var(--surface2); color: var(--text); }
  .settings-body { flex: 1; padding: 20px 24px; display: flex; flex-direction: column; gap: 12px; }
  .settings-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 16px; background: var(--surface); border-radius: 10px; border: 1px solid var(--border); }
  .settings-lbl strong { display: block; font-size: 0.87rem; margin-bottom: 4px; color: var(--text); }
  .settings-lbl span { font-size: 0.75rem; color: var(--text2); line-height: 1.4; }
  .settings-info { padding: 14px 16px; background: var(--surface); border-radius: 10px; border: 1px solid var(--border); }
  .settings-info strong { display: block; font-size: 0.78rem; color: var(--text2); margin-bottom: 6px; text-transform: uppercase; letter-spacing: .05em; }
  .settings-info span { font-size: 0.76rem; color: var(--text); word-break: break-all; font-family: monospace; }
  .tog { flex-shrink: 0; width: 42px; height: 24px; background: var(--border); border-radius: 12px; position: relative; cursor: pointer; transition: background .2s; }
  .tog.on { background: var(--accent); }
  .tog-knob { position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; background: #fff; border-radius: 50%; transition: transform .2s; box-shadow: 0 1px 3px rgba(0,0,0,.25); }
  .tog.on .tog-knob { transform: translateX(18px); }

  /* Layout */
  .container { max-width: 1280px; margin: 0 auto; padding: 20px 24px; }

  /* Navegação por semana */
  .date-nav { display: flex; gap: 6px; margin-bottom: 20px; align-items: stretch; }
  .week-nav-btn { background: var(--surface); border: 1px solid var(--border); color: var(--text2); padding: 0 14px; border-radius: 8px; cursor: pointer; font-size: 1.2rem; line-height: 1; transition: all .15s; flex-shrink: 0; }
  .week-nav-btn:hover { background: var(--accent); border-color: var(--accent); color: white; }
  .week-days { display: flex; gap: 4px; flex: 1; }
  .date-btn { background: var(--surface); border: 1px solid var(--border); color: var(--text2); padding: 7px 4px; border-radius: 8px; cursor: pointer; font-size: 0.68rem; text-align: center; transition: all .15s; flex: 1; text-transform: uppercase; letter-spacing: .04em; line-height: 1.3; }
  .date-btn:hover { background: var(--surface2); color: var(--text); }
  .date-btn.active { background: var(--accent); border-color: var(--accent); color: white; }
  .date-btn.today:not(.active) { border-color: var(--accent); color: var(--accent); }
  .date-btn.no-data { opacity: 0.3; pointer-events: none; }
  .date-day { font-size: 0.78rem; font-weight: 700; display: block; margin-top: 2px; }
  .week-range { font-size: 0.7rem; color: var(--text2); text-align: center; margin-top: -14px; margin-bottom: 14px; letter-spacing: .03em; }
  .week-nav-btn:disabled { opacity: 0.25; cursor: not-allowed; pointer-events: none; }

  /* Summary cards */
  .summary-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin-bottom: 20px; }
  @media(max-width:900px){ .summary-grid{grid-template-columns:repeat(3,1fr);} }
  @media(max-width:500px){ .summary-grid{grid-template-columns:repeat(2,1fr);} }
  .summary-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  .summary-card .label { font-size: 0.7rem; color: var(--text2); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px; }
  .summary-card .value { font-size: 1.5rem; font-weight: 700; line-height: 1; }
  .summary-card .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; vertical-align: middle; }
  .c-meeting { color: var(--meeting); } .dot-meeting { background: var(--meeting); }
  .c-chat { color: var(--chat); } .dot-chat { background: var(--chat); }
  .c-browser { color: var(--browser); } .dot-browser { background: var(--browser); }
  .c-app { color: var(--app); } .dot-app { background: var(--app); }
  .c-idle { color: var(--idle); } .dot-idle { background: var(--idle); }
  .c-active { color: var(--accent); } .dot-active { background: var(--accent); }

  /* Two column layout */
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
  @media(max-width:700px){ .two-col{grid-template-columns:1fr;} }

  /* Top lists */
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
  .panel-title { font-size: 0.82rem; font-weight: 600; color: var(--text2); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 14px; }
  .top-item { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); }
  .top-item:last-child { border-bottom: none; }
  .top-item .name { width: 180px; font-size: 0.82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 0; }
  .top-item .bar-wrap { flex: 1; background: var(--border); border-radius: 4px; height: 5px; overflow: hidden; }
  .top-item .bar { height: 100%; border-radius: 4px; }
  .top-item .dur { width: 52px; text-align: right; font-size: 0.8rem; color: var(--text2); font-family: monospace; flex-shrink: 0; }

  /* Hourly chart */
  .chart-wrap { margin-bottom: 20px; }
  .chart-bars { display: flex; align-items: flex-end; gap: 3px; height: 80px; padding: 0 4px; }
  .chart-bar-col { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px; }
  .chart-bar { width: 100%; border-radius: 3px 3px 0 0; background: var(--accent); opacity: 0.8; min-height: 2px; transition: height .3s; }
  .chart-label { font-size: 0.6rem; color: var(--text2); }

  .settings-field { width: 100%; box-sizing: border-box; background: var(--surface2); border: 1px solid var(--border); border-radius: 8px; color: var(--text); padding: 8px; font-size: 0.78rem; margin-top: 6px; font-family: inherit; }
  .ignored-chip { display: inline-flex; align-items: center; gap: 6px; background: var(--surface2); border: 1px solid var(--border); border-radius: 999px; padding: 4px 6px 4px 12px; font-size: 0.76rem; color: var(--text); }
  .ignored-chip button { background: none; border: none; color: var(--text2); cursor: pointer; font-size: 0.85rem; width: 20px; height: 20px; border-radius: 50%; display: flex; align-items: center; justify-content: center; }
  .ignored-chip button:hover { background: rgba(239,68,68,.2); color: #ef4444; }

  /* Modal de detalhe da atividade (clique numa sessão) */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 600; display: flex; align-items: center; justify-content: center; padding: 20px; animation: fadeIn .15s; }
  .modal-overlay.hidden { display: none; }
  .modal-card { position: relative; background: var(--bg); border: 1px solid var(--border); border-radius: 14px; width: 420px; max-width: 100%; max-height: 86vh; overflow-y: auto; padding: 22px 22px 20px; box-shadow: 0 20px 60px rgba(0,0,0,.5); }
  .modal-title { font-size: 1rem; font-weight: 700; margin-right: 24px; margin-bottom: 8px; }
  .modal-sub { font-size: 0.76rem; color: var(--text2); line-height: 1.6; margin-bottom: 16px; }
  .modal-label { display: block; font-size: 0.72rem; color: var(--text2); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 2px; margin-top: 4px; }
  .modal-actions { display: flex; gap: 8px; margin-top: 10px; }
  .modal-actions .btn { flex: 1; }

  .cat-badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px; font-size: 0.72rem; font-weight: 600; white-space: nowrap; }
  .badge-teams_meeting { background: rgba(139,92,246,.18); color: var(--meeting); }
  .badge-teams_chat { background: rgba(6,182,212,.18); color: var(--chat); }
  .badge-teams_app { background: rgba(124,58,237,.18); color: #a78bfa; }
  .badge-browser { background: rgba(16,185,129,.18); color: var(--browser); }
  .badge-app { background: rgba(245,158,11,.18); color: var(--app); }
  .badge-idle { background: rgba(71,85,105,.18); color: var(--idle); }

  .empty { text-align: center; padding: 48px; color: var(--text2); }

  /* Calendário do dia (FullCalendar vendorizado, tematizado) */
  #week-calendar { --fc-border-color: var(--border); --fc-page-bg-color: transparent; --fc-neutral-bg-color: var(--surface2); --fc-list-event-hover-bg-color: var(--surface2); --fc-today-bg-color: rgba(59,130,246,.08); --fc-event-bg-color: var(--accent); --fc-event-border-color: transparent; color: var(--text); font-family: inherit; }
  #week-calendar .fc-col-header-cell-cushion, #week-calendar .fc-timegrid-slot-label-cushion, #week-calendar .fc-timegrid-axis-cushion { color: var(--text2); font-size: 0.7rem; text-decoration: none; }
  #week-calendar a { color: var(--text); text-decoration: none; }
  #week-calendar .fc-scrollgrid, #week-calendar table { border-color: var(--border) !important; }
  #week-calendar .fc-timegrid-slot, #week-calendar .fc-timegrid-col { border-color: var(--border); }
  /* Dá mais espaço vertical pra hora anterior/atual/seguinte — é onde tem
     mais chance de ter várias atividades concorrentes pra examinar. */
  #week-calendar .fc-timegrid-slot-lane.cal-hour-focus,
  #week-calendar .fc-timegrid-slot-label.cal-hour-focus { height: 90px !important; background: rgba(59,130,246,.06); }
  .fc-sess-event { position: relative; height: 100%; width: 100%; border-radius: 4px; overflow: hidden; padding: 1px 4px; font-size: 0.65rem; color: #fff; cursor: pointer; }
  .fc-sess-bg { position: absolute; inset: 0; opacity: 0.38; }
  .fc-sess-fg { position: absolute; left: 0; right: 0; opacity: 1; }
  .fc-sess-label { position: relative; z-index: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center;gap:12px">
    <svg height="28" viewBox="0 0 967 100" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="TRACKER" style="display:block;max-width:180px;width:auto">
      <path d="M19.7033 1.61764H131.454L111.75 27.2059H76.6077V100H46.1705V27.2059H0L19.7033 1.61764Z" fill="white"/>
      <path d="M252.198 80.8824L270.284 100H229.554L207.645 73.9706H160.887V100H130.45V49.7059H216.027C225.437 49.7059 232.789 45.4412 232.789 38.6765C232.789 31.0294 226.466 27.2059 216.027 27.2059H130.45L150.3 1.61764H213.527C235.877 1.61764 264.109 8.67646 264.109 36.7647C264.109 52.0588 254.551 64.7059 238.377 67.9412C241.759 70.4412 245.435 73.9706 252.198 80.8824Z" fill="white"/>
      <path d="M437.239 100H322.842L341.222 74.7059H365.924C371.806 74.7059 378.717 74.7059 383.422 75C380.775 71.4706 376.658 65.4412 373.424 60.7353L355.779 34.7059L309.608 100H273.584L336.664 11.0294C340.781 5.29411 346.809 0 356.367 0C365.483 0 371.512 4.85294 375.776 11.0294L437.239 100Z" fill="white"/>
      <path d="M489.177 74.7059H558.139L538.582 100H489.177C455.064 100 429.92 78.3824 429.92 49.8529C429.92 21.0294 455.064 1.61764 489.177 1.61764H558.139L538.582 27.2059H489.177C472.709 27.2059 460.357 37.0588 460.357 51.1765C460.357 65.1471 472.562 74.7059 489.177 74.7059Z" fill="white"/>
      <path d="M652.075 67.5L692.805 100H646.782L617.08 74.1176C606.934 65.2941 602.229 61.0294 598.847 57.6471C598.994 62.2059 599.288 67.0588 599.288 71.7647V100H568.704V1.61764H599.288V24.4118C599.288 30.4412 598.994 36.4706 598.7 41.6176C602.523 38.0882 607.816 33.0882 616.786 25.5882L645.164 1.61764H689.276L651.634 29.8529C638.548 39.7059 632.519 44.1176 626.196 47.9412C631.784 51.7647 639.43 57.2059 652.075 67.5Z" fill="white"/>
      <path d="M733.404 74.7059H817.07L797.514 100H702.82V1.61764H816.776L797.073 27.2059H733.404V38.9706H811.335L793.838 61.3235H733.404V74.7059Z" fill="white"/>
      <path d="M948.914 80.8824L967 100H926.27L904.361 73.9706H857.602V100H827.165V49.7059H912.742C922.153 49.7059 929.505 45.4412 929.505 38.6765C929.505 31.0294 923.182 27.2059 912.742 27.2059H827.165L847.016 1.61764H910.243C932.593 1.61764 960.824 8.67646 960.824 36.7647C960.824 52.0588 951.267 64.7059 935.092 67.9412C938.474 70.4412 942.15 73.9706 948.914 80.8824Z" fill="white"/>
    </svg>
    <div class="subtitle">Rastreador automático de atividades</div>
  </div>
  <span class="badge-live">AO VIVO</span>
  <div class="header-actions">
    <button class="btn" onclick="loadData()">&#8635; Atualizar</button>
    <button class="btn btn-green" onclick="exportData()">&#8595; Exportar CSV</button>
    <button class="btn-icon" onclick="openSettings()" title="Configurações">&#9881;</button>
  </div>
</header>

<div id="settings-overlay" class="settings-overlay hidden" onclick="if(event.target===this)closeSettings()">
  <div class="settings-panel">
    <div class="settings-head">
      <h2>Configurações</h2>
      <button class="settings-close" onclick="closeSettings()">&#10005;</button>
    </div>
    <div class="settings-body">
      <div class="settings-row">
        <div class="settings-lbl">
          <strong>Rastrear em segundo plano</strong>
          <span>Continua registrando atividades mesmo com o painel fechado</span>
        </div>
        <div class="tog" id="tog-bg" onclick="toggleBackground()"><div class="tog-knob"></div></div>
      </div>
      <div class="settings-row">
        <div class="settings-lbl">
          <strong>Iniciar no login</strong>
          <span>Abre o painel automaticamente ao iniciar sessão</span>
        </div>
        <div class="tog" id="tog-login" onclick="toggleLogin()"><div class="tog-knob"></div></div>
      </div>
      <div class="settings-info">
        <strong>Arquivo de dados</strong>
        <span id="settings-data-dir">—</span>
      </div>
      <div class="settings-info">
        <strong>Apps ignorados no rastreamento em segundo plano</strong>
        <div id="ignored-chips" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;"></div>
        <div style="display:flex;gap:6px;margin-top:8px;">
          <input id="ignored-new" class="settings-field" style="margin-top:0;" placeholder="Nome do processo (ex: Spotify)">
          <button class="btn" onclick="addIgnoredProcess()">Adicionar</button>
        </div>
      </div>
      <div class="settings-info">
        <strong>Integração Jira / Tempo</strong>
        <input id="jira-url" class="settings-field" placeholder="URL do Jira (ex: https://suaempresa.atlassian.net)">
        <input id="jira-email" class="settings-field" placeholder="Seu e-mail do Jira">
        <input id="jira-token" class="settings-field" type="password" placeholder="API token do Jira">
        <input id="tempo-token" class="settings-field" type="password" placeholder="API token do Tempo">
        <div style="display:flex;gap:8px;margin-top:8px;">
          <button class="btn" onclick="saveJiraConfig()">Salvar</button>
          <button class="btn" onclick="testJiraConnection()">Testar conexão</button>
        </div>
        <span id="jira-status" style="display:block;margin-top:6px;font-size:0.72rem;color:var(--text2);"></span>
      </div>
      <div class="settings-danger">
        <button class="btn btn-danger" onclick="uninstallApp()">Desinstalar Activity Tracker</button>
      </div>
    </div>
  </div>
</div>

<div id="session-modal-overlay" class="modal-overlay hidden" onclick="if(event.target===this)closeSessionModal()">
  <div class="modal-card">
    <button class="settings-close" style="position:absolute;top:14px;right:14px;" onclick="closeSessionModal()">&#10005;</button>
    <div id="modal-body"></div>
  </div>
</div>

<div class="container">
  <div class="date-nav">
    <button class="week-nav-btn" id="btn-prev-week" onclick="shiftWeek(-1)" title="Semana anterior com dados">&#8249;</button>
    <div class="week-days" id="date-nav"></div>
    <button class="week-nav-btn" id="btn-next-week" onclick="shiftWeek(1)" title="Próxima semana com dados">&#8250;</button>
  </div>
  <div id="week-range" class="week-range"></div>

  <div class="panel" id="week-calendar-panel" style="margin-bottom:20px;">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:8px;">
      <div class="panel-title" style="margin-bottom:0">Calendário do dia (clique numa atividade pra ver detalhes)</div>
      <button class="btn" onclick="exportSessionsData()">&#8595; Exportar sessões</button>
    </div>
    <div id="week-calendar"></div>
  </div>

  <div id="content"><div class="empty">Carregando dados...</div></div>
</div>

<script src="/vendor/fullcalendar.min.js"></script>
<script>
let allData = {};
let selectedDate = null;
let currentWeekStart = null;

const DAY_NAMES = ['DOM', 'SEG', 'TER', 'QUA', 'QUI', 'SEX', 'SAB'];

function getWeekStart(dateStr) {
  const d = new Date((dateStr || new Date().toISOString().slice(0,10)) + 'T00:00:00');
  const dow = d.getDay();
  d.setDate(d.getDate() - (dow === 0 ? 6 : dow - 1)); // recua até segunda-feira
  return d.toISOString().slice(0, 10);
}

function addDays(dateStr, n) {
  const d = new Date(dateStr + 'T00:00:00');
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

const CAT_ICONS = {
  teams_meeting: '&#128249;',
  teams_chat: '&#128172;',
  teams_app: '&#128995;',
  browser: '&#127760;',
  app: '&#128187;',
  idle: '&#128164;',
};
const CAT_COLORS = {
  teams_meeting: '#8b5cf6',
  teams_chat: '#06b6d4',
  teams_app: '#7c3aed',
  browser: '#10b981',
  app: '#f59e0b',
  idle: '#475569',
};

function fmtDur(s) {
  if (!s || s <= 0) return '—';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return h + 'h ' + String(m).padStart(2,'0') + 'm';
  if (m > 0) return m + 'm';
  return s + 's';
}

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function loadData() {
  try {
    if (typeof pywebview !== 'undefined' && pywebview.api) {
      allData = await pywebview.api.get_data();
    } else {
      const res = await fetch('/api/data');
      allData = await res.json();
    }
    const dates = Object.keys(allData).sort().reverse();
    if (dates.length === 0) {
      document.getElementById('content').innerHTML = '<div class="empty">Nenhum registro encontrado.<br>Inicie o <strong>INICIAR.bat</strong> para começar a rastrear.</div>';
      return;
    }
    if (!selectedDate || !allData[selectedDate]) selectedDate = dates[0];
    if (!currentWeekStart) currentWeekStart = getWeekStart(selectedDate);
    renderDateNav(dates);
    renderDay(selectedDate);
  } catch(e) {
    document.getElementById('content').innerHTML = '<div class="empty">Erro ao carregar: ' + esc(e.message) + '</div>';
  }
}

function renderDateNav(dates) {
  const today = new Date().toISOString().slice(0, 10);
  const dateset = new Set(dates);
  let html = '';
  for (let i = 0; i < 7; i++) {
    const d = addDays(currentWeekStart, i);
    const dow = new Date(d + 'T00:00:00').getDay();
    const [, m, day] = d.split('-');
    const isActive  = d === selectedDate;
    const isToday   = d === today;
    const hasData   = dateset.has(d);
    const cls = ['date-btn', isActive ? 'active' : '', isToday ? 'today' : '', !hasData ? 'no-data' : ''].filter(Boolean).join(' ');
    html += `<button class="${cls}" onclick="selectDate('${d}')">${DAY_NAMES[dow]}<span class="date-day">${day}/${m}</span></button>`;
  }
  document.getElementById('date-nav').innerHTML = html;
  // Rótulo da semana
  const wEnd = addDays(currentWeekStart, 6);
  const [, sm, sd] = currentWeekStart.split('-');
  const [, em, ed] = wEnd.split('-');
  const rangeEl = document.getElementById('week-range');
  if (rangeEl) rangeEl.textContent = `${sd}/${sm} – ${ed}/${em}`;
  // Desabilita › se já estamos na semana atual
  const nextBtn = document.getElementById('btn-next-week');
  if (nextBtn) {
    const atCurrent = currentWeekStart >= getWeekStart(today);
    nextBtn.disabled = atCurrent;
  }
  updateWeekCalendar();
}

function selectDate(d) {
  selectedDate = d;
  currentWeekStart = getWeekStart(d);
  renderDateNav(Object.keys(allData).sort().reverse());
  renderDay(d);
}

function shiftWeek(dir) {
  const dateset = new Set(Object.keys(allData));
  let tempStart = addDays(currentWeekStart, dir * 7);
  // Pula semanas sem dados (até 104 semanas = 2 anos)
  for (let attempts = 0; attempts < 104; attempts++) {
    let hasData = false;
    for (let i = 0; i < 7; i++) {
      if (dateset.has(addDays(tempStart, i))) { hasData = true; break; }
    }
    if (hasData) break;
    tempStart = addDays(tempStart, dir * 7);
  }
  currentWeekStart = tempStart;
  // Seleciona o dia mais recente com dados na semana
  let best = null;
  for (let i = 6; i >= 0; i--) {
    const d = addDays(currentWeekStart, i);
    if (dateset.has(d)) { best = d; break; }
  }
  if (best) {
    selectedDate = best;
    renderDateNav(Object.keys(allData).sort().reverse());
    renderDay(selectedDate);
  } else {
    renderDateNav(Object.keys(allData).sort().reverse());
    document.getElementById('content').innerHTML = '<div class="empty">Nenhuma atividade registrada nesta semana.<br>Use ‹ › para navegar entre semanas.</div>';
  }
}

async function exportData() {
  if (typeof pywebview !== 'undefined' && pywebview.api) {
    await pywebview.api.export_csv(selectedDate || null); // abre diálogo nativo de salvar
  } else {
    window.location.href = '/export/csv' + (selectedDate ? '?date=' + selectedDate : '');
  }
}

function renderDay(d) {
  const day = allData[d];
  if (!day) {
    document.getElementById('content').innerHTML = '<div class="empty">Nenhum registro para este dia.</div>';
    return;
  }
  const { summary } = day;
  const totals = summary.totals || {};
  const hourly = summary.hourly || {};
  const totalActive = Object.entries(totals).filter(([k]) => k !== 'idle').reduce((a,[,v]) => a+v, 0);

  let html = '';

  // ── Summary cards ──────────────────────────────────────────────────────────
  html += '<div class="summary-grid">';
  const cards = [
    { label: 'Tempo ativo', value: fmtDur(totalActive), cls: 'c-active', dot: 'dot-active' },
    { label: 'Reuniões Teams', value: fmtDur(totals.teams_meeting||0), cls: 'c-meeting', dot: 'dot-meeting' },
    { label: 'Chat Teams', value: fmtDur(totals.teams_chat||0), cls: 'c-chat', dot: 'dot-chat' },
    { label: 'Navegador', value: fmtDur(totals.browser||0), cls: 'c-browser', dot: 'dot-browser' },
    { label: 'Outros apps', value: fmtDur(totals.app||0), cls: 'c-app', dot: 'dot-app' },
    { label: 'Ocioso', value: fmtDur(totals.idle||0), cls: 'c-idle', dot: 'dot-idle' },
  ];
  for (const c of cards) {
    html += `<div class="summary-card"><div class="label"><span class="dot ${c.dot}"></span>${c.label}</div><div class="value ${c.cls}">${c.value}</div></div>`;
  }
  html += '</div>';

  // ── Gráfico por hora ───────────────────────────────────────────────────────
  const hours = Array.from({length: 13}, (_, i) => i + 7); // 7h às 19h
  const maxHourly = Math.max(...hours.map(h => hourly[h] || 0), 1);
  html += '<div class="panel chart-wrap"><div class="panel-title">Atividade por hora</div>';
  html += '<div class="chart-bars">';
  for (const h of hours) {
    const secs = hourly[h] || 0;
    const pct = Math.round((secs / maxHourly) * 100);
    const tip = `${h}h: ${fmtDur(secs)}`;
    html += `<div class="chart-bar-col" title="${tip}">
      <div class="chart-bar" style="height:${pct}%"></div>
      <div class="chart-label">${h}h</div>
    </div>`;
  }
  html += '</div></div>';

  // ── Top listas ─────────────────────────────────────────────────────────────
  html += '<div class="two-col">';
  html += renderTopPanel('Reuniões & Chats Teams', summary.details, ['teams_meeting','teams_chat'], '#8b5cf6', '#06b6d4');
  html += renderTopPanel('Navegador & Aplicativos', summary.details, ['browser','app'], '#10b981', '#f59e0b');
  html += '</div>';

  document.getElementById('content').innerHTML = html;
}

let currentModalRow = null;

// ── Modal de detalhe da sessão (aberta pelo clique num evento do calendário) ─

function renderSessionModal(row) {
  currentModalRow = row;
  const label = row.detail || row.process || '—';
  const totalMin = Math.max(Math.round(row.total / 60), 1);
  const occurrences = [...row.items].sort((a, b) => a.start.localeCompare(b.start));
  const occHtml = occurrences.map(s =>
    `${s.start.slice(11, 16)}–${s.end.slice(11, 16)} — ${fmtDur(s.total_seconds)} aberto, ${fmtDur(s.foreground_seconds)} em foco`
  ).join('<br>');
  const applyLabelRow = row.key ? `
    <label style="display:flex;align-items:flex-start;gap:6px;margin-top:8px;font-size:0.76rem;color:var(--text2);cursor:pointer;">
      <input type="checkbox" id="modal-apply-label" style="margin-top:2px;">
      <span>Usar esse código sempre que aparecer "<strong style="color:var(--text)">${esc(label.slice(0, 40))}</strong>" (inclusive em dias futuros)</span>
    </label>` : '';

  document.getElementById('modal-body').innerHTML = `
    <div class="modal-title">${esc(label)}</div>
    <div class="modal-sub">${occurrences.length} ocorrência(s) — ${fmtDur(row.total)} no total<br>${occHtml}</div>

    <label class="modal-label">Código Jira / Tempo</label>
    <input id="modal-code" class="settings-field" value="${esc(row.code || '')}" placeholder="Ex: PROJ-123">
    ${applyLabelRow}
    <div class="modal-actions">
      <button class="btn" onclick="saveModalCode()">Salvar código</button>
      <button class="btn" style="border-color:#ef4444;color:#ef4444;" onclick="deleteModalSessions()">Excluir</button>
    </div>
    <label style="display:flex;align-items:flex-start;gap:6px;margin-top:8px;font-size:0.76rem;color:var(--text2);cursor:pointer;">
      <input type="checkbox" id="modal-stop-tracking" style="margin-top:2px;">
      <span>Também parar de rastrear "<strong style="color:var(--text)">${esc((row.process || '').slice(0, 40))}</strong>" — some da lista em Configurações → Apps ignorados, onde dá pra reverter a qualquer momento</span>
    </label>

    <hr style="border:none;border-top:1px solid var(--border);margin:16px 0;">

    <label class="modal-label">Enviar apontamento pro Tempo</label>
    <div style="display:flex;gap:8px;">
      <input id="modal-send-code" class="settings-field" style="flex:2" placeholder="Issue (ex: PROJ-123)" value="${esc(row.code || '')}">
      <input id="modal-send-minutes" class="settings-field" style="flex:1" type="number" min="1" value="${totalMin}">
    </div>
    <button class="btn btn-green" style="margin-top:8px;width:100%;" onclick="sendModalWorklog()">Enviar pro Tempo</button>
    <div id="modal-status" style="margin-top:8px;font-size:0.76rem;color:var(--text2);"></div>
  `;
  document.getElementById('session-modal-overlay').classList.remove('hidden');
}

function closeSessionModal() {
  document.getElementById('session-modal-overlay').classList.add('hidden');
  currentModalRow = null;
}

async function saveModalCode() {
  if (!currentModalRow) return;
  const statusEl = document.getElementById('modal-status');
  if (typeof pywebview === 'undefined' || !pywebview.api) { statusEl.textContent = 'Disponível só no aplicativo desktop.'; return; }
  const code = document.getElementById('modal-code').value.trim();
  const applyLabelEl = document.getElementById('modal-apply-label');
  const applyToLabel = applyLabelEl ? applyLabelEl.checked : false;
  await pywebview.api.assign_jira_code(currentModalRow.items.map(i => i.id), code, applyToLabel, currentModalRow.key);
  closeSessionModal();
  loadData();
}

async function deleteModalSessions() {
  if (!currentModalRow) return;
  const label = currentModalRow.detail || currentModalRow.process;
  if (!confirm(`Excluir "${label}" (${currentModalRow.items.length} ocorrência(s))? Essa ação não pode ser desfeita.`)) return;
  if (typeof pywebview === 'undefined' || !pywebview.api) { alert('Disponível só no aplicativo desktop.'); return; }
  const stopTrackingEl = document.getElementById('modal-stop-tracking');
  const stopTracking = stopTrackingEl ? stopTrackingEl.checked : false;
  await pywebview.api.delete_sessions(currentModalRow.items.map(i => i.id));
  if (stopTracking && currentModalRow.process) {
    await pywebview.api.add_ignored_process(currentModalRow.process);
  }
  closeSessionModal();
  loadData();
}

async function sendModalWorklog() {
  if (!currentModalRow) return;
  const statusEl = document.getElementById('modal-status');
  const code = document.getElementById('modal-send-code').value.trim();
  const minutes = parseFloat(document.getElementById('modal-send-minutes').value.replace(',', '.'));
  if (!code || !minutes || minutes <= 0) { statusEl.textContent = 'Preencha o código da issue e uma duração válida.'; return; }
  if (typeof pywebview === 'undefined' || !pywebview.api) { statusEl.textContent = 'Disponível só no aplicativo desktop.'; return; }
  statusEl.textContent = 'Enviando...';
  const seconds = Math.round(minutes * 60);
  const r = await pywebview.api.send_worklog(currentModalRow.items.map(i => i.id), code, selectedDate, seconds, '');
  statusEl.textContent = r.ok ? 'Apontamento enviado ao Tempo!' : ('Erro: ' + r.error);
  if (r.ok) loadData();
}

async function exportSessionsData() {
  if (typeof pywebview !== 'undefined' && pywebview.api) {
    await pywebview.api.export_sessions_csv(selectedDate || null);
  } else {
    window.location.href = '/export/sessions-csv' + (selectedDate ? '?date=' + selectedDate : '');
  }
}

function renderTopPanel(title, details, cats, color1, color2) {
  let items = [];
  for (let i = 0; i < cats.length; i++) {
    const d = details[cats[i]] || {};
    const color = i === 0 ? color1 : color2;
    for (const [name, secs] of Object.entries(d)) {
      items.push({ name, secs, color });
    }
  }
  items.sort((a,b) => b.secs - a.secs);
  items = items.slice(0, 8);
  const maxSecs = items[0]?.secs || 1;

  let html = `<div class="panel"><div class="panel-title">${title}</div>`;
  if (items.length === 0) {
    html += '<div style="color:var(--text2);font-size:.82rem;padding:6px 0">Nenhum registro</div>';
  }
  for (const it of items) {
    const pct = Math.round((it.secs / maxSecs) * 100);
    html += `<div class="top-item">
      <span class="name" title="${esc(it.name)}">${esc(it.name.slice(0,28))}</span>
      <div class="bar-wrap"><div class="bar" style="width:${pct}%;background:${it.color}"></div></div>
      <span class="dur">${fmtDur(it.secs)}</span>
    </div>`;
  }
  html += '</div>';
  return html;
}

// ── Calendário do dia (FullCalendar) ────────────────────────────────────────
let weekCalendar = null;
let weekCalendarShownDate = null;

function initWeekCalendar() {
  if (weekCalendar || typeof FullCalendar === 'undefined') return;
  weekCalendar = new FullCalendar.Calendar(document.getElementById('week-calendar'), {
    initialView: 'timeGridDay',
    headerToolbar: false,
    firstDay: 1,
    slotMinTime: '00:00:00',
    slotMaxTime: '24:00:00',
    slotDuration: '01:00:00',
    expandRows: true,
    slotLabelFormat: { hour: '2-digit', minute: '2-digit', hour12: false },
    height: 620,
    nowIndicator: true,
    allDaySlot: false,
    // Por padrão o FullCalendar empilha eventos concorrentes com leve
    // deslocamento (staggered), o que fica ilegível com várias atividades
    // no mesmo horário. Isso força colunas lado a lado de verdade.
    slotEventOverlap: false,
    eventOrder: (a, b) => (b.extendedProps.session?.total_seconds || 0) - (a.extendedProps.session?.total_seconds || 0),
    dayHeaderContent: (arg) => {
      const day = String(arg.date.getDate()).padStart(2, '0');
      const month = String(arg.date.getMonth() + 1).padStart(2, '0');
      return `${DAY_NAMES[arg.date.getDay()]} ${day}/${month}`;
    },
    events: [],
    eventContent: renderCalendarEventContent,
    eventClick: (info) => {
      const g = info.event.extendedProps.session;
      if (g) renderSessionModal(g);
    },
  });
  weekCalendar.render();
}

function darkenColor(hex, amount) {
  const c = (hex || '#64748b').replace('#', '');
  const num = parseInt(c.length === 3 ? c.split('').map(x => x + x).join('') : c, 16);
  const r = Math.round(((num >> 16) & 0xff) * (1 - amount));
  const g = Math.round(((num >> 8) & 0xff) * (1 - amount));
  const b = Math.round((num & 0xff) * (1 - amount));
  return `rgb(${r},${g},${b})`;
}

function renderCalendarEventContent(arg) {
  const s = arg.event.extendedProps.session;
  const color = CAT_COLORS[s.category] || '#64748b';
  const edgeColor = darkenColor(color, 0.45);
  const startMs = arg.event.start.getTime();
  const endMs = (arg.event.end || arg.event.start).getTime();
  const totalMs = Math.max(endMs - startMs, 1000);
  let fgHtml = '';
  for (const range of (s.foreground_ranges || [])) {
    const fStart = new Date(range[0]).getTime();
    const fEnd = new Date(range[1]).getTime();
    const top = Math.max(((fStart - startMs) / totalMs) * 100, 0);
    const height = Math.max(((fEnd - fStart) / totalMs) * 100, 3);
    fgHtml += `<div class="fc-sess-fg" style="top:${top}%;height:${height}%;background:${color}"></div>`;
  }
  const wrap = document.createElement('div');
  wrap.className = 'fc-sess-event';
  wrap.style.borderLeft = `4px solid ${edgeColor}`;
  wrap.title = `${s.detail || s.process || ''} — ${fmtDur(s.total_seconds)} aberto, ${fmtDur(s.foreground_seconds)} em foco`;
  wrap.innerHTML = `<div class="fc-sess-bg" style="background:${color}"></div>${fgHtml}<div class="fc-sess-label">${esc(s.detail || s.process || '')}</div>`;
  return { domNodes: [wrap] };
}

function groupSessionsByLabel(sessions) {
  // Uma sessão "crua" é criada toda vez que a captura perde e reencontra a
  // mesma janela (falha momentânea do AppleScript, o app foi minimizado e
  // reaberto, o computador dormiu, etc). Em vez de desenhar um evento por
  // sessão crua (o que fragmenta a mesma atividade em vários blocos soltos
  // espalhados pelo dia), agrupamos por nome (processo+categoria+detalhe) e
  // desenhamos UMA barra só, do primeiro ao último horário em que apareceu —
  // com a opacidade marcando onde teve atividade de verdade dentro dela.
  const groups = new Map();
  for (const s of sessions) {
    const key = s.process + '::' + s.category + '::' + s.detail;
    if (!groups.has(key)) {
      groups.set(key, {
        key, process: s.process, category: s.category, detail: s.detail,
        start: s.start, end: s.end,
        total_seconds: 0, foreground_seconds: 0, foreground_ranges: [],
        code: null, items: [],
      });
    }
    const g = groups.get(key);
    if (s.start < g.start) g.start = s.start;
    if (s.end > g.end) g.end = s.end;
    g.total_seconds += s.total_seconds || 0;
    g.foreground_seconds += s.foreground_seconds || 0;
    g.foreground_ranges.push(...(s.foreground_ranges || []));
    if (s.jira_code) g.code = s.jira_code;
    g.items.push(s);
  }
  for (const g of groups.values()) {
    g.foreground_ranges.sort((a, b) => a[0].localeCompare(b[0]));
    g.total = g.total_seconds; // alias esperado pela modal de detalhe
  }
  return Array.from(groups.values());
}

function updateWeekCalendar() {
  if (!weekCalendar) return;
  const events = [];
  const day = allData[selectedDate];
  if (day && day.sessions) {
    for (const g of groupSessionsByLabel(day.sessions)) {
      events.push({ start: g.start, end: g.end, extendedProps: { session: g } });
    }
  }
  weekCalendar.removeAllEventSources();
  weekCalendar.addEventSource(events);
  // Só navega (o que reseta a posição de rolagem) quando o dia exibido
  // realmente mudou — evita o calendário "pular" a cada atualização
  // automática de dados (a cada 15s) enquanto o usuário está no mesmo dia.
  if (weekCalendarShownDate !== selectedDate) {
    weekCalendar.gotoDate(selectedDate);
    weekCalendarShownDate = selectedDate;
  }
  highlightCurrentHourRows();
}

function highlightCurrentHourRows() {
  const el = document.getElementById('week-calendar');
  if (!el) return;
  el.querySelectorAll('.cal-hour-focus').forEach(n => n.classList.remove('cal-hour-focus'));
  const todayStr = new Date().toISOString().slice(0, 10);
  if (selectedDate !== todayStr) return; // só faz sentido destacar "agora" no dia de hoje
  const now = new Date();
  const hours = [now.getHours() - 1, now.getHours(), now.getHours() + 1].filter(h => h >= 0 && h <= 23);
  for (const h of hours) {
    const t = String(h).padStart(2, '0') + ':00:00';
    el.querySelectorAll(`[data-time="${t}"]`).forEach(n => n.classList.add('cal-hour-focus'));
  }
}

// Inicialização: aguarda pywebview estar pronto, ou inicia direto no navegador
let _appStarted = false;
function _startApp() {
  if (_appStarted) return;
  _appStarted = true;
  initWeekCalendar();
  loadData();
  setInterval(loadData, 15000);
}
window.addEventListener('pywebviewready', _startApp);
// Fallback para modo navegador (pywebviewready nunca dispara no browser)
setTimeout(_startApp, 300);

// ── Settings ──────────────────────────────────────────────────────────────────
let _bgEnabled = false;
let _loginEnabled = false;

async function openSettings() {
  document.getElementById('settings-overlay').classList.remove('hidden');
  if (typeof pywebview !== 'undefined' && pywebview.api) {
    const s = await pywebview.api.get_settings();
    _bgEnabled = s.background_mode || false;
    _loginEnabled = s.login_mode || false;
    const togBg = document.getElementById('tog-bg');
    if (_bgEnabled) togBg.classList.add('on'); else togBg.classList.remove('on');
    const togLogin = document.getElementById('tog-login');
    if (_loginEnabled) togLogin.classList.add('on'); else togLogin.classList.remove('on');
    const dd = document.getElementById('settings-data-dir');
    if (dd && s.data_dir) dd.textContent = s.data_dir;
    await refreshIgnoredChips();
    const jc = await pywebview.api.get_jira_config();
    document.getElementById('jira-url').value = jc.base_url || '';
    document.getElementById('jira-email').value = jc.email || '';
    document.getElementById('jira-token').placeholder = jc.has_jira_token ? 'Token salvo (deixe em branco p/ manter)' : 'API token do Jira';
    document.getElementById('tempo-token').placeholder = jc.has_tempo_token ? 'Token salvo (deixe em branco p/ manter)' : 'API token do Tempo';
  }
}
async function saveJiraConfig() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  const base_url = document.getElementById('jira-url').value.trim();
  const email = document.getElementById('jira-email').value.trim();
  const jt = document.getElementById('jira-token').value.trim();
  const tt = document.getElementById('tempo-token').value.trim();
  await pywebview.api.save_jira_config(base_url, email, jt || null, tt || null);
  document.getElementById('jira-token').value = '';
  document.getElementById('tempo-token').value = '';
  document.getElementById('jira-status').textContent = 'Configuração salva.';
}
async function testJiraConnection() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  const statusEl = document.getElementById('jira-status');
  statusEl.textContent = 'Testando...';
  const r = await pywebview.api.test_jira_connection();
  statusEl.textContent = r.ok ? ('Conectado como ' + r.display_name) : ('Erro: ' + r.error);
}
let ignoredProcessesList = [];

async function refreshIgnoredChips() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  ignoredProcessesList = await pywebview.api.get_ignored_processes();
  renderIgnoredChips();
}

function renderIgnoredChips() {
  const el = document.getElementById('ignored-chips');
  if (!el) return;
  if (!ignoredProcessesList || ignoredProcessesList.length === 0) {
    el.innerHTML = '<span style="color:var(--text2);font-size:0.76rem;">Nenhum app ignorado</span>';
    return;
  }
  el.innerHTML = ignoredProcessesList.map(name => `
    <span class="ignored-chip">${esc(name)}<button onclick="removeIgnoredProcess('${esc(name).replace(/'/g, "\\'")}')" title="Voltar a rastrear">&#10005;</button></span>
  `).join('');
}

async function addIgnoredProcess() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  const input = document.getElementById('ignored-new');
  const name = input.value.trim();
  if (!name) return;
  if (!ignoredProcessesList.includes(name.toLowerCase())) {
    ignoredProcessesList = [...ignoredProcessesList, name.toLowerCase()].sort();
  }
  await pywebview.api.save_ignored_processes(ignoredProcessesList);
  input.value = '';
  renderIgnoredChips();
}

async function removeIgnoredProcess(name) {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  ignoredProcessesList = ignoredProcessesList.filter(n => n !== name);
  await pywebview.api.save_ignored_processes(ignoredProcessesList);
  renderIgnoredChips();
}
function closeSettings() {
  document.getElementById('settings-overlay').classList.add('hidden');
}
async function toggleBackground() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  _bgEnabled = !_bgEnabled;
  const tog = document.getElementById('tog-bg');
  if (_bgEnabled) tog.classList.add('on'); else tog.classList.remove('on');
  await pywebview.api.save_setting('background_mode', _bgEnabled);
}
async function toggleLogin() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  _loginEnabled = !_loginEnabled;
  const tog = document.getElementById('tog-login');
  if (_loginEnabled) tog.classList.add('on'); else tog.classList.remove('on');
  await pywebview.api.save_setting('login_mode', _loginEnabled);
}
async function uninstallApp() {
  if (typeof pywebview === 'undefined' || !pywebview.api) {
    alert('Desinstalação só está disponível no aplicativo desktop.');
    return;
  }
  if (!confirm('Tem certeza que deseja desinstalar o Activity Tracker?\n\nO app será fechado e removido do computador.')) return;
  const deleteData = confirm('Deseja também apagar o histórico de atividades registradas?\n\nOK = apagar tudo (não pode ser desfeito)\nCancelar = manter os dados salvos, caso queira reinstalar depois');
  await pywebview.api.uninstall(deleteData);
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", HTML_TEMPLATE.encode("utf-8"))

        elif path == "/api/data":
            date_filter = params.get("date", [None])[0]
            data = get_api_data(date_filter)
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)

        elif path == "/export/csv":
            date_filter = params.get("date", [None])[0]
            csv_data = export_csv(date_filter)
            fname = f"atividades_{date_filter or 'todas'}.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.end_headers()
            self.wfile.write(csv_data.encode("utf-8-sig"))

        elif path == "/vendor/fullcalendar.min.js":
            fpath = VENDOR_DIR / "fullcalendar.min.js"
            if fpath.exists():
                self._send(200, "application/javascript; charset=utf-8", fpath.read_bytes())
            else:
                self._send(404, "text/plain", b"Not found")

        elif path == "/export/sessions-csv":
            date_filter = params.get("date", [None])[0]
            csv_data = export_sessions_csv(date_filter)
            fname = f"sessoes_{date_filter or 'todas'}.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.end_headers()
            self.wfile.write(csv_data.encode("utf-8-sig"))

        else:
            self._send(404, "text/plain", b"Not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # A janela do app carrega sempre pela mesma porta entre uma abertura e
        # outra — sem isso, o WebView do macOS pode servir a página (ou o
        # fullcalendar.min.js) de um cache antigo em vez do build atual.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def export_csv(date_filter=None):
    records = load_records()
    enriched = compute_durations(records)
    if date_filter:
        enriched = [r for r in enriched if r.get("date") == date_filter]

    cat_labels = {
        "teams_meeting": "Reunião Teams",
        "teams_chat": "Chat Teams",
        "teams_app": "Teams (app)",
        "browser": "Navegador",
        "app": "Aplicativo",
        "idle": "Ocioso",
    }

    lines = ["Data,Hora,Categoria,Detalhe,Processo,Duração (min)"]
    for r in enriched:
        cat = cat_labels.get(r.get("category", ""), r.get("category", ""))
        detail = (r.get("detail") or r.get("title", "")).replace('"', '""')
        proc = r.get("process", "").replace('"', '""')
        dur_min = round(r.get("duration_seconds", 0) / 60, 1)
        lines.append(f'{r.get("date","")},{r.get("time","")},"{cat}","{detail}","{proc}",{dur_min}')
    return "\n".join(lines)


def export_sessions_csv(date_filter=None) -> str:
    """Exporta as sessões (motor novo) com o código Jira/Tempo atribuído,
    pronto para apontamento manual — plano B mesmo com a integração via API."""
    sessions = load_sessions()
    codes = load_jira_codes()
    if date_filter:
        sessions = [s for s in sessions if s.get("date") == date_filter]
    sessions.sort(key=lambda s: s.get("start", ""))

    lines = ["Data,Início,Fim,Categoria,Processo,Detalhe,Duração Total (min),Duração em Foco (min),Código Jira/Tempo"]
    for s in sessions:
        code = codes.get(s.get("id"), {}).get("code", "")
        detail = (s.get("detail") or "").replace('"', '""')
        proc = (s.get("process") or "").replace('"', '""')
        total_min = round((s.get("total_seconds") or 0) / 60, 1)
        fg_min = round((s.get("foreground_seconds") or 0) / 60, 1)
        start_t = (s.get("start") or "")[11:19]
        end_t = (s.get("end") or "")[11:19]
        lines.append(
            f'{s.get("date","")},{start_t},{end_t},"{s.get("category","")}","{proc}","{detail}",{total_min},{fg_min},"{code}"'
        )
    return "\n".join(lines)


def start_server():
    """Faz o bind numa porta livre e retorna (server, port) sem bloquear —
    quem chamar decide se roda serve_forever() numa thread própria. Usado
    tanto pelo modo standalone (main()) quanto pela janela do app, que
    precisa saber a porta pra carregar a UI via HTTP (necessário pro
    <script src> do FullCalendar funcionar — carregar via html= puro não
    tem origem HTTP real pra resolver caminhos relativos)."""
    port = PORT
    server = None
    for p in range(port, port + 20):
        try:
            server = HTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    return server, port


def main():
    server, port = start_server()
    if server is None:
        print("[AVISO] Servidor web nao disponivel (todas as portas ocupadas).")
        return
    print("=" * 60)
    print(f"  Activity Tracker - Painel Web")
    print(f"  Acesse: http://localhost:{port}")
    print(f"  Pressione Ctrl+C para parar.")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Servidor encerrado.")


if __name__ == "__main__":
    main()
