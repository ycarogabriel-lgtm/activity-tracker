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
    # Override pra dev/teste — nunca ler/escrever os dados reais do usuário
    # sem querer enquanto o app de verdade também pode estar rodando.
    override = os.environ.get("ACTIVITY_TRACKER_DATA_DIR")
    if override:
        d = Path(override)
        d.mkdir(parents=True, exist_ok=True)
        return d
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
GROUP_OVERRIDES_FILE = SCRIPT_DIR / "group_overrides.json"
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


def load_group_overrides() -> dict:
    """Agrupamento é decisão do usuário, não regra escondida: por padrão as
    sessões viram um bloco só no calendário quando process+category+detail
    batem exatamente — aqui o usuário pode dar um nome de grupo próprio pra
    um rótulo específico, que passa a valer também pra ocorrências futuras."""
    if GROUP_OVERRIDES_FILE.exists():
        try:
            with open(GROUP_OVERRIDES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def set_group_override(label_key: str, group_name: str):
    overrides = load_group_overrides()
    if group_name:
        overrides[label_key] = group_name
    else:
        overrides.pop(label_key, None)
    _atomic_write_json(GROUP_OVERRIDES_FILE, overrides, ensure_ascii=False, indent=2)


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
    group_overrides = load_group_overrides()
    for s in sessions:
        c = codes.get(s.get("id"))
        if c:
            s["jira_code"] = c["code"]
        else:
            lc = label_codes.get(session_label_key(s))
            s["jira_code"] = lc["code"] if lc else None
        s["group_label"] = group_overrides.get(session_label_key(s))
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
  /* Não dá pra usar SF Symbols de verdade aqui — é fonte proprietária da
     Apple, sem API de CSS pra carregar num WebView. Ícones em SVG inline,
     linguagem visual aproximada (traço fino, pontas arredondadas). */
  .icon { width: 1em; height: 1em; vertical-align: -0.15em; flex-shrink: 0; stroke-width: 1.6; }

  /* Escuro é o padrão; :root[data-theme="light"] troca só os tokens.
     --accent é cinza-azulado quase neutro, calibrado pra Lc >= 60 via APCA
     (mínimo real de leitura) mesmo continuando bem dessaturado. */
  :root {
    --ground: #121214;
    --surface: #1a1a1d;
    --surface-2: #232326;
    --surface-3: #2c2c30;
    --border: #38383c;
    --border-soft: #29292c;
    --text: #f5f5f0;
    --text-dim: #c2c2be;
    --text-muted: #b5b5b1;
    --accent: #aab8bd;
    --accent-strong: #8898a0;
    --accent-ink: #121214;
    --surface-glass: rgba(26, 26, 29, 0.72);
    --btn-glass: rgba(40, 40, 44, 0.38);
    --cat-meeting: #4d8dff;
    --cat-chat: #22d3ee;
    --cat-teams-app: #a78bfa;
    --cat-app: #e0a458;
    --cat-idle: #6b6b70;
    --danger: #f58080;
    --danger-border: #e05252;
    --mono: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", monospace;
    --sans: -apple-system, "Segoe UI", system-ui, sans-serif;
  }
  :root[data-theme="light"] {
    --ground: #f4f4f2;
    --surface: #ffffff;
    --surface-2: #ececea;
    --surface-3: #e2e2df;
    --border: #cfcfca;
    --border-soft: #dededa;
    --text: #1c1c1a;
    --text-dim: #4a4a46;
    --text-muted: #67675f;
    --accent: #4d5760;
    --accent-strong: #3d454c;
    --accent-ink: #ffffff;
    --surface-glass: rgba(255, 255, 255, 0.72);
    --btn-glass: rgba(255, 255, 255, 0.42);
    --cat-meeting: #2b62c9;
    --cat-chat: #0d8fa6;
    --cat-teams-app: #7c5cd6;
    --cat-app: #a9701f;
    --cat-idle: #85857c;
    --danger: #c23a3a;
    --danger-border: #c23a3a;
  }

  * { box-sizing: border-box; }
  html { background: var(--ground); }
  body {
    margin: 0;
    background: var(--ground);
    color: var(--text);
    font-family: var(--sans);
    font-size: 15px;
    line-height: 1.45;
    position: relative;
  }
  /* Sem isso, o backdrop-filter do vidro não tem nada variado atrás pra
     borrar — vira só uma cor chapada semi-transparente. Manchas suaves e
     bem apagadas dão profundidade real pra vibrância pegar. */
  body::before {
    content: '';
    position: fixed; inset: 0; z-index: 0;
    background:
      radial-gradient(680px 480px at 8% -6%, color-mix(in srgb, var(--accent) 20%, transparent), transparent 60%),
      radial-gradient(620px 440px at 96% 12%, color-mix(in srgb, var(--cat-meeting) 12%, transparent), transparent 55%),
      radial-gradient(720px 520px at 30% 108%, color-mix(in srgb, var(--cat-chat) 10%, transparent), transparent 55%);
    pointer-events: none;
  }
  * { scrollbar-width: thin; scrollbar-color: var(--border) transparent; }

  /* !important de propósito: .hidden precisa vencer qualquer display:flex/grid
     de regras mais específicas ou definidas depois no arquivo — senão a
     ordem no CSS decide silenciosamente quem apareceria, não a classe. */
  .hidden { display: none !important; }

  /* ── Casca de app desktop ─────────────────────────────────────────────────
     Sidebar fixa (não navbar de site), toolbar estreita com vidro/vibrância.
     Cantos de janela e traffic lights vêm do chrome nativo do SO de verdade
     (pywebview já desenha isso) — não replicados aqui dentro. */
  .app-shell { display: flex; min-height: 100vh; position: relative; z-index: 1; }
  /* Ocupa espaço de verdade no layout (o conteúdo se ajusta ao lado dela,
     não fica por baixo) — só o VISUAL é "descolado" da referência: margem
     nas bordas + cantos arredondados, como um painel flutuante à parte,
     não uma coluna encostada nas bordas da janela. */
  .sidebar {
    width: 208px; flex-shrink: 0;
    background: var(--surface-glass);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    backdrop-filter: blur(24px) saturate(180%);
    border: 1px solid var(--border-soft);
    border-radius: 16px;
    box-shadow: 0 8px 28px rgba(0,0,0,.28);
    display: flex; flex-direction: column; padding: 18px 12px;
    margin: 13px;
    position: fixed; top: 0; bottom: 0;
    transition: width .16s ease, margin .16s ease, padding .16s ease, opacity .12s ease;
    overflow: hidden;
  }
  .sidebar.sidebar-closed {
    width: 0; margin-left: 0; padding-left: 0; padding-right: 0; border-width: 0; opacity: 0;
  }
  .sidebar-logo { padding: 4px 8px 22px; display: block; max-width: 100%; overflow: hidden; }
  .sidebar-logo svg { display: block; height: 16px; width: auto; max-width: 100%; color: var(--text); }
  .side-nav { display: flex; flex-direction: column; gap: 2px; }
  /* Mesmo espaçamento (padding, min-height) dos botões da toolbar — dois
     paddings diferentes pra elementos clicáveis do mesmo tipo (padding/
     altura) não faziam sentido dentro do mesmo app. */
  .side-item {
    display: flex; align-items: center; gap: 10px; width: 100%;
    padding: 7px 14px; min-height: 32px; box-sizing: border-box;
    border-radius: 8px; background: none; border: none;
    color: var(--text-dim); font-size: 13.5px; font-weight: 600; font-family: var(--sans);
    text-align: left; cursor: pointer;
  }
  .side-item svg { width: 16px; height: 16px; flex-shrink: 0; }
  .side-item:hover { background: var(--surface-2); color: var(--text); }
  .side-item.active { background: var(--surface-3); color: var(--accent); }
  .side-item.active svg { color: var(--accent); }
  .tab-badge { font-family: var(--mono); font-size: 11px; background: var(--surface-3); color: var(--text-muted); padding: 1px 7px; border-radius: 999px; margin-left: auto; }
  .side-item.active .tab-badge { background: var(--ground); color: var(--accent); }

  /* Sidebar é fixed (fora do fluxo) — reserva o espaço dela aqui manualmente
     (208px + 12px de margem dos dois lados). Recolhe quando ela fecha. */
  .main-col { flex: 1; min-width: 0; display: flex; flex-direction: column; margin-left: 234px; transition: margin-left .16s ease; }
  .sidebar.sidebar-closed + .main-col { margin-left: 0; }
  /* Só agrupamento em linha — sem caixa, sem vidro, sem borda própria aqui.
     Os botões é que flutuam soltos (vidro + sombra ficam neles, não numa
     barra por trás agrupando tudo). */
  /* sticky de propósito: os botões ficam por cima enquanto a página rola —
     é isso que faz o vidro deles ter algo colorido passando por baixo pra
     borrar de verdade, não só uma cor chapada parada. */
  .toolbar {
    display: flex; align-items: center; gap: 12px;
    padding: 13px 24px; flex-shrink: 0;
    position: sticky; top: 0; z-index: 3;
  }
  .toolbar-actions { margin-left: auto; display: flex; gap: 8px; }

  /* Cápsula no estilo da toolbar do Mail/macOS: flutua sozinho, com vidro +
     sombra nele mesmo já em repouso (não só no hover). --btn-glass é bem
     mais transparente que --surface-glass (sidebar/modal) de propósito —
     esses têm texto denso e precisam ficar legíveis; o botão precisa
     deixar a cor de trás aparecer de verdade pra parecer vidro. */
  .btn {
    background: var(--btn-glass);
    -webkit-backdrop-filter: blur(16px) saturate(180%);
    backdrop-filter: blur(16px) saturate(180%);
    border: 1px solid var(--border-soft); color: var(--text-dim);
    box-shadow: 0 1px 3px rgba(0,0,0,.18), 0 1px 1px rgba(0,0,0,.1);
    padding: 7px 14px; border-radius: 999px; font-size: 13px; font-weight: 500; cursor: pointer;
    min-height: 32px; display: inline-flex; align-items: center; gap: 6px; font-family: var(--sans);
    transition: background .1s, transform .1s, color .1s, border-color .1s;
  }
  .btn:hover { background: var(--surface-3); color: var(--text); }
  .btn:active { background: var(--surface-2); transform: scale(.96); }
  .btn-primary { background: var(--accent); color: var(--accent-ink); font-weight: 700; border-color: var(--accent); }
  .btn-primary:hover { background: var(--accent-strong); color: var(--accent-ink); }
  .btn-primary:active { background: var(--accent-strong); transform: scale(.96); }
  .btn-danger-o { color: var(--danger); }
  .btn-danger-o:hover { background: color-mix(in srgb, var(--danger) 16%, transparent); color: var(--danger); }
  /* Mesmo padrão do .btn: vidro + sombra já em repouso, não só no hover. */
  .btn-icon {
    width: 32px; height: 32px; padding: 0; justify-content: center;
    background: var(--btn-glass);
    -webkit-backdrop-filter: blur(16px) saturate(180%);
    backdrop-filter: blur(16px) saturate(180%);
    border: 1px solid var(--border-soft);
    box-shadow: 0 1px 3px rgba(0,0,0,.18), 0 1px 1px rgba(0,0,0,.1);
    border-radius: 50%; color: var(--text-dim); cursor: pointer;
    transition: background .1s, transform .1s, color .1s;
  }
  .btn-icon:hover { background: var(--surface-3); color: var(--text); }
  .btn-icon:active { background: var(--surface-2); transform: scale(.92); }

  main { padding: 20px 24px 48px; width: 100%; }

  .status-banner {
    display: flex; align-items: center; gap: 12px;
    background: #2e2210; border: 1px solid var(--accent-strong); color: var(--text);
    margin: 16px 24px 0; padding: 12px 16px; border-radius: 10px; font-size: 13.5px;
  }
  .status-banner svg { color: var(--accent); flex-shrink: 0; width: 20px; height: 20px; }
  .status-text { flex: 1; }
  .status-text strong { font-weight: 700; }
  .status-action { flex-shrink: 0; border-color: var(--accent); color: var(--accent); }
  /* Pausa manual não é erro — banner neutro, nunca amarelo/erro. */
  .status-banner-manual { background: var(--surface); border-color: var(--border); }
  .status-banner-manual svg { color: var(--text-muted); }

  .daynav { display: flex; gap: 8px; align-items: stretch; margin: 16px 24px 0; }
  .day-arrow {
    background: var(--btn-glass);
    -webkit-backdrop-filter: blur(16px) saturate(180%);
    backdrop-filter: blur(16px) saturate(180%);
    border: 1px solid var(--border-soft); border-radius: 50%;
    box-shadow: 0 1px 3px rgba(0,0,0,.18), 0 1px 1px rgba(0,0,0,.1);
    color: var(--text-muted); width: 40px; height: 40px; cursor: pointer; display: flex; align-items: center; justify-content: center;
    transition: background .1s, transform .1s, color .1s, border-color .1s;
  }
  .day-arrow:hover { color: var(--text); background: var(--surface-3); }
  .day-arrow:active { transform: scale(.92); }
  .day-arrow:disabled { opacity: .25; cursor: not-allowed; }
  .days { display: flex; gap: 6px; flex: 1; }
  .date-btn {
    flex: 1; background: transparent; border: 1px solid var(--border-soft); border-radius: 10px;
    padding: 8px 4px; text-align: center; cursor: pointer; min-height: 50px; color: var(--text-dim); font-family: var(--sans);
  }
  .date-btn:hover { background: var(--surface-2); color: var(--text); }
  .date-btn .dow { font-size: 11px; letter-spacing: .06em; text-transform: uppercase; display: block; color: var(--text-muted); }
  .date-btn .date-day { font-family: var(--mono); font-size: 15px; font-weight: 600; margin-top: 2px; display: block; }
  .date-btn.today:not(.active) { border-color: var(--accent); }
  .date-btn.today:not(.active) .date-day { color: var(--accent); }
  .date-btn.active { background: var(--accent); border-color: var(--accent); }
  .date-btn.active .dow, .date-btn.active .date-day { color: var(--accent-ink); }
  .date-btn.no-data { opacity: .3; pointer-events: none; }

  .section-head { display: flex; align-items: center; gap: 12px; justify-content: space-between; margin: 20px 0 16px; }
  .section-title { font-size: 17px; font-weight: 700; }
  /* min-height/padding batendo exatamente com .btn — ficavam com alturas
     visualmente diferentes na mesma linha (section-head). */
  .cal-filter { display: flex; align-items: center; gap: 8px; background: var(--surface); border: 1px solid var(--border-soft); border-radius: 8px; padding: 7px 14px; min-height: 32px; box-sizing: border-box; max-width: 340px; flex: 1; }
  .cal-filter svg { color: var(--text-muted); flex-shrink: 0; }
  .cal-filter input { background: none; border: none; outline: none; color: var(--text); font-size: 13.5px; width: 100%; font-family: var(--sans); }
  .cal-filter input::placeholder { color: var(--text-muted); }

  .panel { background: var(--surface); border: 1px solid var(--border-soft); border-radius: 14px; padding: 16px; }
  .panel-title { font-size: 12.5px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 14px; }

  /* Resumo — hero (tempo ativo hoje) + grade 2x2 de métricas secundárias,
     igual à proposta de redesign: superfície plana (sem vidro — vidro é só
     pra sidebar/toolbar/modal, que ficam por cima de conteúdo colorido). */
  .dot-meeting { background: var(--cat-meeting); } .dot-chat { background: var(--cat-chat); }
  .dot-browser { background: var(--accent); } .dot-app { background: var(--cat-app); }
  .dot-idle { background: var(--cat-idle); } .dot-active { background: var(--accent); }

  .hero-row { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; margin-bottom: 32px; }
  @media(max-width:760px){ .hero-row{grid-template-columns:1fr;} }
  .hero-card { background: var(--surface); border: 1px solid var(--border-soft); border-radius: 16px; padding: 28px; }
  .hero-label { font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700; }
  .hero-value { font-family: var(--mono); font-size: 56px; font-weight: 700; color: var(--accent); margin-top: 10px; line-height: 1; font-variant-numeric: tabular-nums; }
  .hero-sub { color: var(--text-dim); font-size: 13.5px; margin-top: 12px; }

  .stat-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
  .stat { background: var(--surface); border: 1px solid var(--border-soft); border-radius: 12px; padding: 16px; }
  .stat-label { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--text-muted); font-weight: 600; }
  .stat-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
  .stat-value { font-family: var(--mono); font-size: 25px; font-weight: 700; margin-top: 8px; font-variant-numeric: tabular-nums; color: var(--text); }

  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; margin-top: 32px; }
  @media(max-width:760px){ .two-col{grid-template-columns:1fr;} }
  .two-col-title { font-size: 14px; font-weight: 700; margin-bottom: 12px; color: var(--text); }
  .top-item { display: flex; align-items: center; gap: 14px; padding: 11px 0; border-bottom: 1px solid var(--border-soft); }
  .top-item:last-child { border-bottom: none; }
  .top-item .name { width: 170px; font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 0; }
  .top-item .bar-wrap { flex: 1; background: var(--surface-2); border-radius: 4px; height: 6px; overflow: hidden; }
  .top-item .bar { height: 100%; border-radius: 4px; }
  .top-item .dur { font-family: var(--mono); font-size: 13px; color: var(--text-dim); width: 56px; text-align: right; flex-shrink: 0; font-variant-numeric: tabular-nums; }

  .chart-wrap { margin-bottom: 20px; }
  .chart-bars { display: flex; gap: 3px; height: 80px; padding: 0 4px; }
  /* height:100% aqui é o que faz o height:X% do .chart-bar (definido via JS)
     ter uma referência de verdade pra resolver contra — sem isso a % fica
     inválida (sem altura de pai definida) e todas as barras colapsam pro
     min-height, ficando visualmente idênticas/achatadas. */
  .chart-bar-col { flex: 1; height: 100%; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 4px; }
  .chart-bar { width: 100%; border-radius: 3px 3px 0 0; background: var(--accent); opacity: .75; min-height: 2px; transition: height .3s; }
  .chart-label { font-size: 10.5px; color: var(--text-muted); font-family: var(--mono); }

  .empty { text-align: center; padding: 48px; color: var(--text-muted); }

  /* Calendário do dia (FullCalendar vendorizado, tematizado com os novos tokens) */
  #week-calendar { --fc-border-color: var(--border-soft); --fc-page-bg-color: transparent; --fc-neutral-bg-color: var(--surface-2); --fc-list-event-hover-bg-color: var(--surface-2); --fc-today-bg-color: transparent; --fc-event-bg-color: var(--accent); --fc-event-border-color: transparent; color: var(--text); font-family: inherit; }
  #week-calendar .fc-col-header-cell-cushion, #week-calendar .fc-timegrid-slot-label-cushion, #week-calendar .fc-timegrid-axis-cushion { color: var(--text-muted); font-size: 11px; text-decoration: none; font-family: var(--mono); }
  #week-calendar a { color: var(--text); text-decoration: none; }
  #week-calendar .fc-scrollgrid, #week-calendar table { border-color: var(--border-soft) !important; }
  #week-calendar .fc-timegrid-slot, #week-calendar .fc-timegrid-col { border-color: var(--border-soft); }
  #week-calendar .fc-timegrid-now-indicator-line { border-color: var(--accent); }
  #week-calendar .fc-timegrid-now-indicator-arrow { border-color: var(--accent); color: var(--accent); }
  /* Destaque só de cor (sem mexer em altura — tentamos redimensionar a
     célula real do FullCalendar via CSS e provou ser instável com
     expandRows:true, o valor nunca batia com o declarado de forma
     confiável). Ambiente pra hora atual, um pouco mais forte no hover. */
  #week-calendar .fc-timegrid-slot-lane.cal-hour-focus,
  #week-calendar .fc-timegrid-slot-label.cal-hour-focus { background: color-mix(in srgb, var(--accent) 6%, transparent); }
  #week-calendar .fc-timegrid-slot-lane.cal-hour-hover,
  #week-calendar .fc-timegrid-slot-label.cal-hour-hover { background: var(--surface-3); }
  .fc-sess-event { position: relative; height: 100%; width: 100%; border-radius: 6px; overflow: hidden; padding: 1px; font-size: .65rem; color: #fff; cursor: pointer; }
  .fc-sess-bg { position: absolute; inset: 0; opacity: .18; }
  .fc-sess-fg { position: absolute; left: 0; right: 0; opacity: 1; }
  .fc-sess-content { position: relative; z-index: 1; background: rgba(10,10,12,.82); padding: 4px 7px; display: inline-block; max-width: 100%; border-radius: 0 0 6px 0; }
  .fc-sess-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 700; display: block; color: #fff; }

  .legend { display: flex; gap: 20px; margin-top: 16px; flex-wrap: wrap; }
  .legend-item { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--text-dim); font-weight: 500; }
  .legend-swatch { width: 11px; height: 11px; border-radius: 3px; flex-shrink: 0; }

  /* Configurações — agrupada em blocos nomeados (Miller, 1956) */
  .cfg-group { background: var(--surface); border: 1px solid var(--border-soft); border-radius: 14px; padding: 18px 20px; margin-bottom: 16px; max-width: 640px; }
  .cfg-group-head { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 700; margin-bottom: 12px; }
  .cfg-group-head svg { color: var(--accent); width: 17px; height: 17px; }
  .cfg-danger .cfg-group-head svg { color: var(--danger); }
  .cfg-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 0; border-top: 1px solid var(--border-soft); }
  .cfg-row:first-of-type { border-top: none; }
  .cfg-row strong { display: block; font-size: 13.5px; }
  .cfg-row span { display: block; font-size: 12.5px; color: var(--text-muted); margin-top: 2px; }
  .mono-path { font-family: var(--mono); font-size: 11.5px !important; word-break: break-all; }
  .cfg-field { width: 100%; box-sizing: border-box; background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px; color: var(--text); padding: 8px 10px; font-size: 13px; margin-top: 8px; font-family: var(--sans); }
  .toggle { width: 40px; height: 23px; border-radius: 999px; background: var(--surface-3); position: relative; flex-shrink: 0; cursor: pointer; }
  .toggle::after { content: ''; position: absolute; top: 2px; left: 2px; width: 19px; height: 19px; border-radius: 50%; background: var(--text-muted); transition: all .15s; }
  .toggle.on { background: var(--accent); }
  .toggle.on::after { left: 19px; background: var(--accent-ink); }
  .status-pill { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 999px; }
  .status-pill.ok { background: var(--accent-ink); color: var(--accent); }
  .seg { display: flex; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; flex-shrink: 0; }
  .seg-btn { background: transparent; border: none; border-left: 1px solid var(--border); color: var(--text-dim); font-family: var(--sans); font-size: 12.5px; font-weight: 600; padding: 6px 14px; cursor: pointer; }
  .seg-btn:first-child { border-left: none; }
  .seg-btn.active { background: var(--surface-3); color: var(--text); }
  /* span.ignored-chip (não só .ignored-chip): os chips ficam dentro de um
     .cfg-row, e ".cfg-row span { display:block }" (mais específico —
     classe+elemento vence classe sozinha) empilhava nome e × verticalmente
     em vez de lado a lado. */
  span.ignored-chip { display: inline-flex; align-items: center; gap: 6px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px; padding: 4px 6px 4px 12px; font-size: 12px; color: var(--text); }
  .ignored-chip button { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 13px; width: 18px; height: 18px; border-radius: 50%; display: flex; align-items: center; justify-content: center; }
  .ignored-chip button:hover { background: color-mix(in srgb, var(--danger) 20%, transparent); color: var(--danger); }

  /* Sheet de detalhe da sessão — vidro flutuante, o elemento mais
     "liquid glass" do sistema: translúcido, brilho fino no topo, sombra
     funda embaixo pra parecer que está pairando sobre o resto. */
  .modal-overlay {
    position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; z-index: 600; padding: 20px;
    background: rgba(0,0,0,.32);
    -webkit-backdrop-filter: blur(6px);
    backdrop-filter: blur(6px);
  }
  .modal-overlay.hidden { display: none; }
  /* Altura fixa (não max-height por conteúdo) — trocar de aba não pode mudar
     o tamanho da janela, só o que aparece dentro dela. Cada painel de aba
     cuida do próprio scroll interno se precisar (.modal-tab-panel). */
  .modal-card {
    position: relative; background: var(--surface-glass);
    -webkit-backdrop-filter: blur(30px) saturate(180%);
    backdrop-filter: blur(30px) saturate(180%);
    border: 1px solid var(--border);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 24px 60px rgba(0,0,0,.35);
    border-radius: 18px; padding: 26px; width: min(640px, 92vw); height: min(520px, 86vh);
    display: flex; flex-direction: column; overflow: hidden;
  }
  /* #modal-body precisa ser flex column também — sem isso os filhos
     (.modal-head/.modal-tabs/.modal-tab-panel) não têm um container flex de
     verdade pra distribuir o espaço, e o painel de aba não estica nem rola:
     conteúdo que passa da altura fixa do card fica cortado, inacessível. */
  #modal-body { display: flex; flex-direction: column; flex: 1; min-height: 0; }
  .modal-tab-panel { flex: 1; min-height: 0; overflow-y: auto; }
  .modal-close { position: absolute; top: 16px; right: 16px; }
  .modal-delete-trigger { position: absolute; top: 16px; right: 58px; color: var(--danger); }
  .modal-head { padding-right: 92px; flex-shrink: 0; }
  .modal-title { font-size: 17px; font-weight: 700; }
  .modal-sub { font-family: var(--mono); font-size: 12px; color: var(--text-muted); margin-top: 6px; line-height: 1.6; }
  .modal-section { border-top: 1px solid var(--border-soft); margin-top: 18px; padding-top: 18px; }
  .modal-section:first-child { border-top: none; margin-top: 0; padding-top: 0; }
  .modal-section-head { display: flex; align-items: center; gap: 7px; font-size: 12.5px; font-weight: 700; color: var(--text-dim); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 10px; }
  .modal-section-head svg { color: var(--accent); width: 15px; height: 15px; }
  .modal-input { width: 100%; box-sizing: border-box; background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px; color: var(--text); padding: 9px 12px; font-size: 13.5px; font-family: var(--sans); }
  .modal-send-row { display: flex; gap: 8px; align-items: center; }
  .modal-hint { font-size: 11.5px; color: var(--text-muted); margin-top: 6px; }
  .modal-check { display: flex; align-items: flex-start; gap: 7px; font-size: 12.5px; color: var(--text-dim); margin-top: 10px; cursor: pointer; }

  /* Confirmação de exclusão inline (não é mais uma seção/aba própria — o
     ícone de lixeira no cabeçalho já resolve, sem precisar de espaço fixo
     dedicado a uma ação que só acontece às vezes). */
  .modal-delete-confirm { background: var(--surface-2); border: 1px solid var(--danger-border); border-radius: 10px; padding: 12px 14px; margin: 14px 0; flex-shrink: 0; }
  .modal-error { background: color-mix(in srgb, var(--danger) 14%, var(--surface-2)); border: 1px solid var(--danger-border); color: var(--danger); border-radius: 8px; padding: 10px 12px; font-size: 12.5px; margin-top: 14px; flex-shrink: 0; }
  .modal-delete-text { font-size: 12.5px; color: var(--text-dim); margin-bottom: 10px; }
  .modal-delete-actions { display: flex; gap: 8px; }
  .modal-delete-actions .btn { flex: 1; justify-content: center; }

  .modal-tabs { margin: 16px 0; width: 100%; flex-shrink: 0; }
  .modal-tabs .seg-btn { flex: 1; text-align: center; }

  /* Switch de rastrear/parar — independente de excluir: mantém o histórico
     já registrado, só para de capturar coisa nova daqui pra frente. */
  .modal-track-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; background: var(--surface-2); border: 1px solid var(--border-soft); border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; cursor: pointer; }
  .modal-track-row strong { display: block; font-size: 13px; }
  .modal-track-row span { display: block; font-size: 11.5px; color: var(--text-muted); margin-top: 2px; }

  /* Grupo é uma entidade de verdade (junta atividades diferentes), não um
     campo de texto solto — na prática são poucos grupos por vez (2-3
     projetos), então é uma LISTA pra escolher, não um campo pra digitar. */
  .modal-group-list { display: flex; flex-direction: column; gap: 6px; }
  .modal-group-option {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    width: 100%; background: var(--surface-2); border: 1px solid var(--border-soft); border-radius: 8px;
    padding: 9px 9px 9px 14px; font-size: 13.5px; font-family: var(--sans); color: var(--text-dim);
    transition: background .1s, border-color .1s, color .1s;
  }
  .modal-group-option.active { background: color-mix(in srgb, var(--accent) 14%, var(--surface-2)); border-color: var(--accent); color: var(--text); font-weight: 600; }
  .modal-group-option svg { width: 15px; height: 15px; color: var(--accent); flex-shrink: 0; vertical-align: -2px; }
  .modal-group-create { margin-top: 10px; }
  .modal-group-create-toggle { background: none; border: none; color: var(--text-muted); font-size: 12.5px; font-family: var(--sans); cursor: pointer; padding: 4px 0; }
  .modal-group-create-toggle:hover { color: var(--text-dim); }
  .modal-group-members { display: flex; flex-direction: column; gap: 4px; }
  .modal-group-member { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12.5px; color: var(--text-dim); padding: 6px 10px; background: var(--surface-2); border-radius: 6px; }

  /* Ocorrências agrupadas por proximidade (>=15min de intervalo real vira um
     cluster novo) — colapsadas por padrão, lista de texto simples, não a
     parede de dezenas de linhas de antes. */
  .occ-clusters { max-height: 320px; overflow-y: auto; border: 1px solid var(--border-soft); border-radius: 10px; }
  .occ-cluster { border-top: 1px solid var(--border-soft); }
  .occ-cluster:first-child { border-top: none; }
  .occ-cluster-head {
    width: 100%; display: flex; align-items: flex-start; justify-content: space-between; gap: 8px;
    background: none; border: none; color: var(--text-dim); font-family: var(--mono); font-size: 12px;
    padding: 8px 10px; cursor: pointer; text-align: left;
  }
  .occ-cluster-head:hover { background: var(--surface-2); color: var(--text); }
  .occ-cluster-head-main { flex: 1; min-width: 0; }
  .occ-chevron { flex-shrink: 0; margin-top: 1px; transition: transform .12s; color: var(--text-muted) !important; }
  .occ-cluster-body { padding: 0 10px 8px 20px; font-family: var(--mono); font-size: 11.5px; color: var(--text-muted); }
  .occ-row { padding: 4px 0; }

  /* Mesma linguagem visual do bloco do calendário (fundo fraco = só aberto,
     trecho cheio = em foco de verdade) — só que como uma barra horizontal
     fina, pra caber numa linha de texto em vez de um bloco de tempo. */
  .occ-focus-bar { position: relative; height: 4px; border-radius: 2px; background: var(--surface-3); overflow: hidden; margin-top: 5px; }
  .occ-focus-bg { position: absolute; inset: 0; opacity: .18; }
  .occ-focus-fg { position: absolute; top: 0; bottom: 0; opacity: 1; }
</style>
</head>
<body>

<div class="app-shell">
  <aside class="sidebar">
    <div class="sidebar-logo">
      <svg viewBox="0 0 967 100" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="TRACKER">
        <path d="M19.7033 1.61764H131.454L111.75 27.2059H76.6077V100H46.1705V27.2059H0L19.7033 1.61764Z" fill="currentColor"/>
        <path d="M252.198 80.8824L270.284 100H229.554L207.645 73.9706H160.887V100H130.45V49.7059H216.027C225.437 49.7059 232.789 45.4412 232.789 38.6765C232.789 31.0294 226.466 27.2059 216.027 27.2059H130.45L150.3 1.61764H213.527C235.877 1.61764 264.109 8.67646 264.109 36.7647C264.109 52.0588 254.551 64.7059 238.377 67.9412C241.759 70.4412 245.435 73.9706 252.198 80.8824Z" fill="currentColor"/>
        <path d="M437.239 100H322.842L341.222 74.7059H365.924C371.806 74.7059 378.717 74.7059 383.422 75C380.775 71.4706 376.658 65.4412 373.424 60.7353L355.779 34.7059L309.608 100H273.584L336.664 11.0294C340.781 5.29411 346.809 0 356.367 0C365.483 0 371.512 4.85294 375.776 11.0294L437.239 100Z" fill="currentColor"/>
        <path d="M489.177 74.7059H558.139L538.582 100H489.177C455.064 100 429.92 78.3824 429.92 49.8529C429.92 21.0294 455.064 1.61764 489.177 1.61764H558.139L538.582 27.2059H489.177C472.709 27.2059 460.357 37.0588 460.357 51.1765C460.357 65.1471 472.562 74.7059 489.177 74.7059Z" fill="currentColor"/>
        <path d="M652.075 67.5L692.805 100H646.782L617.08 74.1176C606.934 65.2941 602.229 61.0294 598.847 57.6471C598.994 62.2059 599.288 67.0588 599.288 71.7647V100H568.704V1.61764H599.288V24.4118C599.288 30.4412 598.994 36.4706 598.7 41.6176C602.523 38.0882 607.816 33.0882 616.786 25.5882L645.164 1.61764H689.276L651.634 29.8529C638.548 39.7059 632.519 44.1176 626.196 47.9412C631.784 51.7647 639.43 57.2059 652.075 67.5Z" fill="currentColor"/>
        <path d="M733.404 74.7059H817.07L797.514 100H702.82V1.61764H816.776L797.073 27.2059H733.404V38.9706H811.335L793.838 61.3235H733.404V74.7059Z" fill="currentColor"/>
        <path d="M948.914 80.8824L967 100H926.27L904.361 73.9706H857.602V100H827.165V49.7059H912.742C922.153 49.7059 929.505 45.4412 929.505 38.6765C929.505 31.0294 923.182 27.2059 912.742 27.2059H827.165L847.016 1.61764H910.243C932.593 1.61764 960.824 8.67646 960.824 36.7647C960.824 52.0588 951.267 64.7059 935.092 67.9412C938.474 70.4412 942.15 73.9706 948.914 80.8824Z" fill="currentColor"/>
      </svg>
    </div>
    <nav class="side-nav">
      <button class="side-item active" onclick="showView('cal')"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg> Calendário</button>
      <button class="side-item" onclick="showView('res')"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg> Resumo</button>
      <button class="side-item" onclick="showView('cfg')"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg> Configurações</button>
    </nav>
  </aside>

  <div class="main-col">
    <div class="toolbar">
      <button class="btn-icon" id="btn-sidebar-toggle" onclick="toggleSidebar()" aria-label="Mostrar/esconder barra lateral"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="9" y1="4" x2="9" y2="20"/></svg></button>
      <div class="toolbar-actions">
        <!-- Pausar/retomar é escolha do usuário — diferente do banner de erro
             (permissão do SO revogada), que é a captura parando sozinha. -->
        <button class="btn" id="btn-pause-toggle" onclick="toggleCapture()">
          <svg class="icon" id="icon-pause-toggle" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
          <span id="label-pause-toggle">Pausar captura</span>
        </button>
        <button class="btn" onclick="loadData()"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg> Atualizar</button>
        <button class="btn btn-primary" onclick="exportData()"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z"/><polyline points="13 3 13 9 19 9"/><path d="M9 15h6"/><polyline points="13 12 16 15 13 18"/></svg> Exportar CSV</button>
      </div>
    </div>

    <div class="status-banner hidden" id="status-banner">
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      <div class="status-text"><strong>Captura pausada.</strong> <span id="status-banner-text">O sistema revogou uma permissão necessária — nenhuma atividade nova está sendo registrada.</span></div>
      <button class="btn status-action" onclick="openSettingsFromBanner()">Corrigir agora</button>
    </div>
    <div class="status-banner status-banner-manual hidden" id="status-banner-manual">
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
      <div class="status-text"><strong>Captura pausada por você.</strong> Nenhuma atividade nova está sendo registrada até você retomar.</div>
      <button class="btn status-action" onclick="toggleCapture()">Retomar captura</button>
    </div>

    <div class="daynav" id="daynav-wrap">
      <button class="day-arrow" id="btn-prev-week" onclick="shiftWeek(-1)" title="Semana anterior com dados"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 6 9 12 15 18"/></svg></button>
      <div class="days" id="date-nav"></div>
      <button class="day-arrow" id="btn-next-week" onclick="shiftWeek(1)" title="Próxima semana com dados"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 6 15 12 9 18"/></svg></button>
    </div>

    <main>
      <div id="view-cal">
        <div class="section-head">
          <div class="cal-filter">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" id="cal-filter-input" oninput="applyCalFilter()" placeholder="Filtrar por nome da atividade..." aria-label="Filtrar atividades do dia">
          </div>
          <button class="btn" onclick="exportSessionsData()"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z"/><polyline points="13 3 13 9 19 9"/><path d="M9 15h6"/><polyline points="13 12 16 15 13 18"/></svg> Exportar sessões</button>
        </div>
        <div class="panel" id="week-calendar-panel">
          <div id="week-calendar"></div>
        </div>
        <div class="legend">
          <div class="legend-item"><span class="legend-swatch" style="background:var(--accent)"></span>Navegador / App</div>
          <div class="legend-item"><span class="legend-swatch" style="background:var(--cat-meeting)"></span>Reunião Teams</div>
          <div class="legend-item"><span class="legend-swatch" style="background:var(--cat-chat)"></span>Chat Teams</div>
          <div class="legend-item"><span class="legend-swatch" style="background:var(--cat-teams-app)"></span>Teams (app)</div>
          <div class="legend-item"><span class="legend-swatch" style="background:var(--cat-idle)"></span>Ocioso</div>
        </div>
      </div>

      <div id="view-res" class="hidden">
        <div id="content"><div class="empty">Carregando dados...</div></div>
      </div>

      <!-- Miller (1956): mais de 5-7 itens simultâneos sobrecarrega memória
           de trabalho — os controles viram grupos nomeados em vez de uma
           lista solta, então a pessoa só precisa lembrar "em qual grupo",
           não escanear tudo de uma vez. -->
      <div id="view-cfg" class="hidden">
        <div class="cfg-group">
          <div class="cfg-group-head"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg> Aparência</div>
          <div class="cfg-row">
            <div><strong>Tema</strong><span>Escuro por padrão — claro pra ambientes muito iluminados</span></div>
            <div class="seg" role="radiogroup" aria-label="Tema">
              <button class="seg-btn active" id="theme-btn-dark" onclick="setTheme('dark')">Escuro</button>
              <button class="seg-btn" id="theme-btn-light" onclick="setTheme('light')">Claro</button>
            </div>
          </div>
        </div>

        <div class="cfg-group">
          <div class="cfg-group-head"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg> Rastreamento</div>
          <div class="cfg-row">
            <div><strong>Rastrear em segundo plano</strong><span>Continua registrando mesmo com o painel fechado</span></div>
            <div class="toggle" id="tog-bg" onclick="toggleBackground()"></div>
          </div>
          <div class="cfg-row">
            <div><strong>Iniciar no login</strong><span>Abre o painel automaticamente ao iniciar sessão</span></div>
            <div class="toggle" id="tog-login" onclick="toggleLogin()"></div>
          </div>
          <div class="cfg-row" style="display:block;">
            <strong>Apps ignorados no rastreamento em segundo plano</strong>
            <div id="ignored-chips" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;"></div>
            <div style="display:flex;gap:6px;margin-top:10px;">
              <input id="ignored-new" class="cfg-field" style="margin-top:0;" placeholder="Nome do processo (ex: Spotify)">
              <button class="btn" onclick="addIgnoredProcess()">Adicionar</button>
            </div>
          </div>
        </div>

        <div class="cfg-group">
          <div class="cfg-group-head"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l2.83-2.83a5 5 0 0 0-7.07-7.07l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7.07 0l-2.83 2.83a5 5 0 0 0 7.07 7.07l1.5-1.5"/></svg> Integração Jira / Tempo</div>
          <!-- Resumo por padrão (proposta de redesign) — só expande o
               formulário de credenciais quando a pessoa pede, em vez de
               deixar 4 campos + botões sempre expostos na tela principal. -->
          <div class="cfg-row">
            <div><strong>Conta conectada</strong><span id="jira-account-status">Nenhuma conta conectada</span></div>
            <button class="btn" style="padding:6px 12px;" onclick="toggleJiraForm()">Configurar</button>
          </div>
          <div class="hidden" id="jira-form-box" style="margin-top:4px;">
            <input id="jira-url" class="cfg-field" placeholder="URL do Jira (ex: https://suaempresa.atlassian.net)">
            <input id="jira-email" class="cfg-field" placeholder="Seu e-mail do Jira">
            <input id="jira-token" class="cfg-field" type="password" placeholder="API token do Jira">
            <input id="tempo-token" class="cfg-field" type="password" placeholder="API token do Tempo">
            <div style="display:flex;gap:8px;margin-top:10px;">
              <button class="btn" onclick="saveJiraConfig()">Salvar</button>
              <button class="btn" onclick="testJiraConnection()">Testar conexão</button>
            </div>
            <span id="jira-status" class="modal-hint" style="display:block;margin-top:8px;"></span>
          </div>
        </div>

        <!-- Agrupamento é decisão do usuário, aberta — não regra escondida:
             ele quem escolhe o nome de grupo direto no card de cada
             atividade (no calendário), não numa lista central editável
             aqui. -->
        <div class="cfg-group">
          <div class="cfg-group-head"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg> Agrupamento de atividades</div>
          <div class="cfg-row">
            <div><strong>Regra padrão</strong><span>Agrupa por aplicativo + categoria automaticamente</span></div>
          </div>
          <div class="cfg-row">
            <div><strong>Nome personalizado</strong><span>Clique numa atividade no calendário e edite o nome do grupo ali — vale também pras próximas vezes que ela aparecer</span></div>
          </div>
        </div>

        <div class="cfg-group">
          <div class="cfg-group-head"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.66 3.58 3 8 3s8-1.34 8-3V6"/><path d="M4 12v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/></svg> Dados</div>
          <div class="cfg-row">
            <div><strong>Arquivo de dados</strong><span class="mono-path" id="settings-data-dir">—</span></div>
          </div>
        </div>

        <div class="cfg-group cfg-danger">
          <div class="cfg-group-head"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> Zona de risco</div>
          <div class="cfg-row">
            <div><strong>Desinstalar Activity Tracker</strong><span>Remove o app e, se você quiser, o histórico</span></div>
            <button class="btn btn-danger-o" onclick="uninstallApp()">Desinstalar</button>
          </div>
        </div>
      </div>
    </main>
  </div>
</div>

<div id="session-modal-overlay" class="modal-overlay hidden" onclick="if(event.target===this)closeSessionModal()">
  <div class="modal-card">
    <button class="btn-icon modal-delete-trigger" aria-label="Excluir" onclick="toggleModalDeleteConfirm()"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg></button>
    <button class="btn-icon modal-close" aria-label="Fechar" onclick="closeSessionModal()"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
    <div id="modal-body"></div>
  </div>
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
  d.setDate(d.getDate() - (dow === 0 ? 6 : dow - 1));
  return d.toISOString().slice(0, 10);
}

function addDays(dateStr, n) {
  const d = new Date(dateStr + 'T00:00:00');
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

const CAT_COLORS = {
  teams_meeting: 'var(--cat-meeting)',
  teams_chat: 'var(--cat-chat)',
  teams_app: 'var(--cat-teams-app)',
  browser: 'var(--accent)',
  app: 'var(--cat-app)',
  idle: 'var(--cat-idle)',
};
function catColor(cat) {
  // resolve a var() pro hex de verdade — precisamos do valor real pra
  // desenhar dentro do canvas/DOM do FullCalendar, não só referenciar CSS.
  return getComputedStyle(document.documentElement).getPropertyValue(
    { teams_meeting: '--cat-meeting', teams_chat: '--cat-chat', teams_app: '--cat-teams-app', browser: '--accent', app: '--cat-app', idle: '--cat-idle' }[cat] || '--cat-idle'
  ).trim();
}

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
      document.getElementById('content').innerHTML = '<div class="empty">Nenhum registro encontrado.</div>';
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
    html += `<button class="${cls}" onclick="selectDate('${d}')"><span class="dow">${DAY_NAMES[dow]}</span><span class="date-day">${day}/${m}</span></button>`;
  }
  document.getElementById('date-nav').innerHTML = html;
  const nextBtn = document.getElementById('btn-next-week');
  if (nextBtn) nextBtn.disabled = currentWeekStart >= getWeekStart(today);
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
  for (let attempts = 0; attempts < 104; attempts++) {
    let hasData = false;
    for (let i = 0; i < 7; i++) {
      if (dateset.has(addDays(tempStart, i))) { hasData = true; break; }
    }
    if (hasData) break;
    tempStart = addDays(tempStart, dir * 7);
  }
  currentWeekStart = tempStart;
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
    document.getElementById('content').innerHTML = '<div class="empty">Nenhuma atividade registrada nesta semana.</div>';
  }
}

async function exportData() {
  if (typeof pywebview !== 'undefined' && pywebview.api) {
    await pywebview.api.export_csv(selectedDate || null);
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

  const idle = totals.idle || 0;
  const trackedTotal = totalActive + idle;
  const activePct = trackedTotal > 0 ? Math.round((totalActive / trackedTotal) * 100) : 0;
  const trabalho = (totals.browser || 0) + (totals.app || 0);

  html += `<div class="hero-row">
    <div class="hero-card">
      <div class="hero-label">Tempo ativo hoje</div>
      <div class="hero-value">${fmtDur(totalActive)}</div>
      <div class="hero-sub">${activePct}% do dia rastreado passou com alguma atividade em foco — pronto pra apontar no Tempo</div>
    </div>
    <div class="stat-grid">
      <div class="stat"><div class="stat-label"><span class="stat-dot dot-meeting"></span>Reuniões</div><div class="stat-value">${totals.teams_meeting ? fmtDur(totals.teams_meeting) : '—'}</div></div>
      <div class="stat"><div class="stat-label"><span class="stat-dot dot-chat"></span>Chat</div><div class="stat-value">${totals.teams_chat ? fmtDur(totals.teams_chat) : '—'}</div></div>
      <div class="stat"><div class="stat-label"><span class="stat-dot dot-browser"></span>Trabalho</div><div class="stat-value">${trabalho ? fmtDur(trabalho) : '—'}</div></div>
      <div class="stat"><div class="stat-label"><span class="stat-dot dot-idle"></span>Ocioso</div><div class="stat-value">${idle ? fmtDur(idle) : '—'}</div></div>
    </div>
  </div>`;

  const hours = Array.from({length: 13}, (_, i) => i + 7);
  const maxHourly = Math.max(...hours.map(h => hourly[h] || 0), 1);
  html += '<div class="panel chart-wrap"><div class="panel-title">Atividade por hora</div>';
  html += '<div class="chart-bars">';
  for (const h of hours) {
    const secs = hourly[h] || 0;
    const pct = Math.round((secs / maxHourly) * 100);
    html += `<div class="chart-bar-col" title="${h}h: ${fmtDur(secs)}">
      <div class="chart-bar" style="height:${pct}%"></div>
      <div class="chart-label">${h}h</div>
    </div>`;
  }
  html += '</div></div>';

  html += '<div class="two-col">';
  html += renderTopPanel('Reuniões e chats', summary.details, ['teams_meeting','teams_chat'], 'var(--cat-meeting)', 'var(--cat-chat)');
  html += renderTopPanel('Navegador e aplicativos', summary.details, ['browser','app'], 'var(--accent)', 'var(--cat-app)');
  html += '</div>';

  document.getElementById('content').innerHTML = html;
}

let currentModalRow = null;

// Funde ocorrências cruas cujo intervalo entre uma e a próxima seja menor
// que gapMinutes num único cluster de exibição — mesmo critério de
// continuidade usado no motor de captura (tracker.py: SESSION_GAP_SECONDS),
// não um número arbitrário diferente. É rede de segurança pra dados antigos
// já fragmentados; dados novos já vêm com bem menos ocorrências brutas.
function clusterOccurrences(items, gapMinutes = 15) {
  const sorted = [...items].sort((a, b) => a.start.localeCompare(b.start));
  const gapMs = gapMinutes * 60 * 1000;
  const clusters = [];
  for (const s of sorted) {
    const last = clusters[clusters.length - 1];
    if (last && new Date(s.start) - new Date(last.end) <= gapMs) {
      if (s.end > last.end) last.end = s.end;
      last.total_seconds += s.total_seconds || 0;
      last.foreground_seconds += s.foreground_seconds || 0;
      last.items.push(s);
    } else {
      clusters.push({ start: s.start, end: s.end, total_seconds: s.total_seconds || 0, foreground_seconds: s.foreground_seconds || 0, items: [s] });
    }
  }
  return clusters;
}

function toggleOccCluster(i) {
  const body = document.getElementById('occ-cluster-' + i);
  const chevron = document.getElementById('occ-chevron-' + i);
  const nowHidden = body.classList.toggle('hidden');
  chevron.style.transform = nowHidden ? '' : 'rotate(180deg)';
}

// Mesma leitura visual do bloco do calendário (fundo fraco = só aberto,
// trecho cheio = em foco de verdade), só que como barra horizontal fina
// pra caber numa linha de texto na modal.
function renderFocusBar(start, end, foregroundRanges, color) {
  const startMs = new Date(start).getTime();
  const endMs = new Date(end).getTime();
  const totalMs = Math.max(endMs - startMs, 1000);
  let fg = '';
  for (const range of (foregroundRanges || [])) {
    const fStart = new Date(range[0]).getTime();
    const fEnd = new Date(range[1]).getTime();
    const left = Math.max(((fStart - startMs) / totalMs) * 100, 0);
    const width = Math.max(((fEnd - fStart) / totalMs) * 100, 1.5);
    fg += `<div class="occ-focus-fg" style="left:${left}%;width:${width}%;background:${color}"></div>`;
  }
  return `<div class="occ-focus-bar"><div class="occ-focus-bg" style="background:${color}"></div>${fg}</div>`;
}

// A janela de teste no navegador comum não tem a ponte do pywebview — sem
// isso, toda ação da modal (excluir, agrupar, código, rastrear) ficava
// silenciosamente sem efeito nenhum, porque é assim que os guards
// "typeof pywebview === 'undefined'" foram desenhados pra se comportar.
// callApi tenta a API nativa primeiro e cai pra um endpoint HTTP equivalente
// quando ela não existe, pra essas ações funcionarem de verdade também fora
// do app empacotado.
async function callApi(pywebviewFn, endpoint, body) {
  if (typeof pywebview !== 'undefined' && pywebview.api) {
    return await pywebviewFn();
  }
  const res = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

let modalErrorText = '';
function renderModalError() {
  if (!modalErrorText) return '';
  return `<div class="modal-error">${esc(modalErrorText)}</div>`;
}

let modalTab = 'org';
let modalDeleteConfirmOpen = false;
// Nomes de processo que não estão mais sendo capturados (independe de
// excluir dados já registrados) — carregado sob demanda, não em toda
// renderização, porque é uma chamada assíncrona pra API do pywebview.
let modalIgnoredProcesses = new Set();
// Nomes de grupo já usados em QUALQUER atividade — é isso que torna o campo
// de agrupamento útil de verdade: um grupo junta atividades DIFERENTES (não
// é só renomear a atividade atual) — precisa mostrar quem mais já está no
// grupo, não só um campo de texto solto que não deixa ver o resultado.
let modalGroupOverrides = {}; // label_key -> nome do grupo
let modalGroupNames = []; // nomes de grupo únicos, pra sugerir

// "Chrome::browser::Site X" -> "Chrome — Site X" — só pra exibição, já que a
// modal não tem acesso aos dados crus de outras atividades (só a chave).
function friendlyFromKey(key) {
  const parts = (key || '').split('::');
  const process = parts[0] || '';
  const detail = parts[2] || '';
  if (process && detail && process !== detail) return `${process} — ${detail}`;
  return detail || process || key;
}

async function refreshModalIgnoredState() {
  try {
    const list = typeof pywebview !== 'undefined' && pywebview.api
      ? await pywebview.api.get_ignored_processes()
      : await (await fetch('/api/ignored_processes')).json();
    modalIgnoredProcesses = new Set((list || []).map(p => p.toLowerCase()));
    if (currentModalRow) renderModalBody();
  } catch (err) { /* não crítico pra abrir a modal */ }
}

async function refreshModalGroupNames() {
  try {
    modalGroupOverrides = (typeof pywebview !== 'undefined' && pywebview.api
      ? await pywebview.api.get_group_overrides()
      : await (await fetch('/api/group_overrides')).json()) || {};
    modalGroupNames = [...new Set(Object.values(modalGroupOverrides))].sort();
    if (currentModalRow) renderModalBody();
  } catch (err) { /* não crítico pra abrir a modal */ }
}

function renderSessionModal(row) {
  currentModalRow = row;
  modalTab = 'org';
  modalDeleteConfirmOpen = false;
  modalErrorText = '';
  renderModalBody();
  document.getElementById('session-modal-overlay').classList.remove('hidden');
  refreshModalIgnoredState();
  refreshModalGroupNames();
}

function switchModalTab(name) {
  modalTab = name;
  renderModalBody();
}

function toggleModalDeleteConfirm() {
  if (!currentModalRow) return;
  modalDeleteConfirmOpen = !modalDeleteConfirmOpen;
  renderModalBody();
}

async function toggleModalTracking() {
  if (!currentModalRow) return;
  const process = currentModalRow.process;
  if (!process) return;
  const isIgnored = modalIgnoredProcesses.has(process.toLowerCase());
  try {
    await callApi(
      () => isIgnored ? pywebview.api.remove_ignored_process(process) : pywebview.api.add_ignored_process(process),
      '/api/set_ignored_process',
      { name: process, ignored: !isIgnored }
    );
    await refreshModalIgnoredState();
  } catch (err) {
    modalErrorText = 'Erro ao mudar rastreamento: ' + err.message;
    renderModalBody();
  }
}

// "Registros" — histórico de ocorrências dessa atividade, já abertas por
// padrão (agora que a modal tem espaço de sobra com as abas). Junto, o
// switch de rastrear: para de capturar coisa nova sem mexer no que já foi
// registrado (diferente de excluir).
function renderRecordsTab(row, color) {
  const isIgnored = modalIgnoredProcesses.has((row.process || '').toLowerCase());
  const trackRow = row.process ? `
    <div class="modal-track-row" onclick="toggleModalTracking()">
      <div>
        <strong>Rastrear esta atividade</strong>
        <span>${isIgnored ? 'Desativado — nada de novo está sendo registrado' : 'Ativo — continua sendo registrado normalmente'}</span>
      </div>
      <div class="toggle ${isIgnored ? '' : 'on'}"></div>
    </div>` : '';
  const hm = (iso) => iso.slice(11, 16);
  const clusters = clusterOccurrences(row.items);
  const clustersHtml = clusters.map((c, i) => `
    <div class="occ-cluster">
      <button type="button" class="occ-cluster-head" onclick="toggleOccCluster(${i})">
        <div class="occ-cluster-head-main">
          <span>${hm(c.start)}–${hm(c.end)} · ${c.items.length} ocorrência${c.items.length > 1 ? 's' : ''} · ${fmtDur(c.foreground_seconds)} em foco</span>
          ${renderFocusBar(c.start, c.end, c.items.flatMap(s => s.foreground_ranges || []), color)}
        </div>
        <svg class="icon occ-chevron" id="occ-chevron-${i}" style="transform:rotate(180deg)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>
      </button>
      <div class="occ-cluster-body" id="occ-cluster-${i}">
        ${c.items.map(s => `
          <div class="occ-row">
            <div>${hm(s.start)}–${hm(s.end)} — ${fmtDur(s.total_seconds)} no total, ${fmtDur(s.foreground_seconds)} em foco</div>
            ${renderFocusBar(s.start, s.end, s.foreground_ranges, color)}
          </div>`).join('')}
      </div>
    </div>`).join('');
  return `${trackRow}<div class="occ-clusters">${clustersHtml}</div>`;
}

function renderOrgTab(row, label) {
  const applyLabelRow = row.key ? `
    <label class="modal-check"><input type="checkbox" id="modal-apply-label"> <span>Usar esse código sempre que aparecer "<strong style="color:var(--text)">${esc(label.slice(0, 40))}</strong>" (inclusive em dias futuros)</span></label>` : '';
  return `
    <div class="modal-section">
      <div class="modal-section-head"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.59 13.41 12 22l-9-9V4a2 2 0 0 1 2-2h9l6.59 6.59a2 2 0 0 1 0 2.82z"/><circle cx="7.5" cy="7.5" r="1.5"/></svg> Código Jira / Tempo</div>
      <input id="modal-code" class="modal-input" value="${esc(row.code || '')}" placeholder="Ex: PROJ-123">
      ${applyLabelRow}
      <button class="btn" style="margin-top:10px;width:100%;justify-content:center;" onclick="saveModalCode()">Salvar código</button>
      <div id="modal-code-status" class="modal-hint"></div>
    </div>`;
}

// Grupo é uma entidade de verdade (junta atividades DIFERENTES pra apontar
// hora junto), e na prática o usuário tem pouquíssimos grupos por vez (ex.:
// 2-3 projetos por semana) — a interação certa é ESCOLHER de uma lista
// curta, não digitar/lembrar um nome exato num campo de texto solto.
function renderGroupTab(row, label) {
  const currentGroup = row.groupLabel || null;
  const otherMembers = currentGroup
    ? Object.entries(modalGroupOverrides).filter(([k, v]) => v === currentGroup && k !== row.key)
    : [];

  const listHtml = modalGroupNames.length ? `
    <div class="modal-group-list">
      ${modalGroupNames.map(name => {
        const isActive = name === currentGroup;
        return `
        <div class="modal-group-option ${isActive ? 'active' : ''}">
          <span>${esc(name)}${isActive ? ' <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>' : ''}</span>
          <button type="button" class="btn" style="padding:6px 16px;flex-shrink:0;" onclick="assignModalGroup(${esc(JSON.stringify(name))})">${isActive ? 'Sair' : 'Entrar'}</button>
        </div>`;
      }).join('')}
    </div>` : `<div class="modal-hint" style="margin-top:0;">Nenhum grupo criado ainda — crie um abaixo.</div>`;

  return `
    <div class="modal-section">
      <div class="modal-section-head"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg> Grupo</div>
      <div class="modal-hint" style="margin-top:0;margin-bottom:10px;">Atividades diferentes no mesmo grupo viram uma coisa só — no calendário e pra apontar horas juntas.</div>
      ${listHtml}
      <div class="modal-group-create">
        <button type="button" class="modal-group-create-toggle" onclick="this.nextElementSibling.classList.toggle('hidden')">+ Criar novo grupo</button>
        <div class="hidden">
          <input id="modal-group-name" class="modal-input" placeholder="Nome do novo grupo" style="margin-top:8px;">
          <button class="btn" style="margin-top:8px;width:100%;justify-content:center;" onclick="saveModalGroup()">Criar e entrar</button>
        </div>
      </div>
      <div id="modal-group-status" class="modal-hint"></div>
      ${currentGroup && otherMembers.length ? `
        <div class="modal-hint" style="margin-top:16px;margin-bottom:6px;">Outras atividades neste grupo:</div>
        <div class="modal-group-members">
          ${otherMembers.map(([k]) => `
            <div class="modal-group-member">
              <span>${esc(friendlyFromKey(k))}</span>
              <button class="btn-icon" style="width:22px;height:22px;flex-shrink:0;" aria-label="Remover do grupo" onclick="ungroupModalMember(${esc(JSON.stringify(k))})"><svg class="icon" style="width:11px;height:11px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            </div>`).join('')}
        </div>` : ''}
    </div>`;
}

function renderSendTab(row, totalMin) {
  return `
    <div class="modal-section">
      <div class="modal-section-head"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg> Apontar no Tempo</div>
      <div class="modal-send-row">
        <input id="modal-send-code" class="modal-input" style="flex:2" placeholder="Issue (ex: PROJ-123)" value="${esc(row.code || '')}">
        <input id="modal-send-minutes" class="modal-input" style="flex:1" type="number" min="0" step="any" value="${totalMin}" data-unit="min">
        <select id="modal-send-unit" class="modal-input" style="flex:0 0 68px;padding:9px 8px;" onchange="onModalSendUnitChange()">
          <option value="min">min</option>
          <option value="h">h</option>
        </select>
      </div>
      <button class="btn btn-primary" style="margin-top:10px;width:100%;justify-content:center;" onclick="sendModalWorklog()">Enviar apontamento</button>
      <div id="modal-status" class="modal-hint"></div>
    </div>`;
}

function onModalSendUnitChange() {
  const input = document.getElementById('modal-send-minutes');
  const newUnit = document.getElementById('modal-send-unit').value;
  const oldUnit = input.dataset.unit || 'min';
  if (oldUnit === newUnit) return;
  const raw = parseFloat((input.value || '0').replace(',', '.')) || 0;
  const minutes = oldUnit === 'h' ? raw * 60 : raw;
  input.value = newUnit === 'h' ? Math.round((minutes / 60) * 100) / 100 : Math.round(minutes);
  input.dataset.unit = newUnit;
}

function renderModalBody() {
  const row = currentModalRow;
  if (!row) return;
  const label = row.displayLabel || row.groupLabel || row.detail || row.process || '—';
  const totalMin = Math.max(Math.round(row.total / 60), 1);
  const color = catColor(row.category);

  const tabHtml = modalTab === 'send' ? renderSendTab(row, totalMin)
    : modalTab === 'grp' ? renderGroupTab(row, label)
    : modalTab === 'rec' ? renderRecordsTab(row, color)
    : renderOrgTab(row, label);

  // Confirmação de exclusão trata só de excluir — o switch de rastrear já
  // vive standalone em Registros; duplicar aqui misturava duas ações
  // diferentes (apagar dados vs. pausar captura futura) no mesmo lugar.
  const deleteConfirmHtml = modalDeleteConfirmOpen ? `
    <div class="modal-delete-confirm">
      <div class="modal-delete-text">Excluir "${esc(label)}" (${row.items.length} ocorrência(s))? Essa ação não pode ser desfeita.</div>
      <div class="modal-delete-actions">
        <button class="btn" onclick="toggleModalDeleteConfirm()">Cancelar</button>
        <button class="btn" id="modal-delete-confirm-btn" style="border-color:var(--danger-border);color:var(--danger);" onclick="deleteModalSessions()">Excluir</button>
      </div>
    </div>` : '';

  document.getElementById('modal-body').innerHTML = `
    <div class="modal-head">
      <div class="modal-title">${esc(label)}</div>
      <div class="modal-sub">${row.items.length} ocorrência(s) — ${fmtDur(row.total)} no total</div>
    </div>

    ${renderModalError()}
    ${deleteConfirmHtml}

    <div class="seg modal-tabs" role="tablist">
      <button class="seg-btn ${modalTab === 'org' ? 'active' : ''}" onclick="switchModalTab('org')">Organizar</button>
      <button class="seg-btn ${modalTab === 'send' ? 'active' : ''}" onclick="switchModalTab('send')">Apontar</button>
      <button class="seg-btn ${modalTab === 'grp' ? 'active' : ''}" onclick="switchModalTab('grp')">Grupo</button>
      <button class="seg-btn ${modalTab === 'rec' ? 'active' : ''}" onclick="switchModalTab('rec')">Registros</button>
    </div>

    <div class="modal-tab-panel">${tabHtml}</div>
  `;
}

function closeSessionModal() {
  document.getElementById('session-modal-overlay').classList.add('hidden');
  currentModalRow = null;
}
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSessionModal(); });

async function saveModalCode() {
  if (!currentModalRow) return;
  const statusEl = document.getElementById('modal-code-status');
  const code = document.getElementById('modal-code').value.trim();
  const applyLabelEl = document.getElementById('modal-apply-label');
  const applyToLabel = applyLabelEl ? applyLabelEl.checked : false;
  const sessionIds = currentModalRow.items.map(i => i.id);
  try {
    await callApi(
      () => pywebview.api.assign_jira_code(sessionIds, code, applyToLabel, currentModalRow.key),
      '/api/assign_jira_code',
      { session_ids: sessionIds, code, apply_to_label: applyToLabel, label_key: currentModalRow.key }
    );
    // Feedback visível antes de fechar — fechar direto sem nenhum sinal fazia
    // parecer que o clique não tinha feito nada.
    if (statusEl) statusEl.textContent = 'Salvo!';
    await new Promise(r => setTimeout(r, 700));
    closeSessionModal();
    loadData();
  } catch (err) {
    if (statusEl) statusEl.textContent = 'Erro: ' + err.message;
  }
}

async function saveModalGroup() {
  if (!currentModalRow) return;
  const statusEl = document.getElementById('modal-group-status');
  const name = document.getElementById('modal-group-name').value.trim();
  if (!name) return;
  try {
    await callApi(
      () => pywebview.api.set_group_override(currentModalRow.key, name),
      '/api/set_group_override',
      { label_key: currentModalRow.key, group_name: name }
    );
    if (statusEl) statusEl.textContent = 'Salvo!';
    await new Promise(r => setTimeout(r, 700));
    closeSessionModal();
    loadData();
  } catch (err) {
    if (statusEl) statusEl.textContent = 'Erro: ' + err.message;
  }
}

// Clicar num grupo da lista entra nele; clicar de novo no que já está ativo
// sai dele — mesmo padrão de um grupo de rádio com deseleção.
async function assignModalGroup(name) {
  if (!currentModalRow) return;
  const isSame = name === currentModalRow.groupLabel;
  const groupName = isSame ? '' : name;
  try {
    await callApi(
      () => pywebview.api.set_group_override(currentModalRow.key, groupName),
      '/api/set_group_override',
      { label_key: currentModalRow.key, group_name: groupName }
    );
    closeSessionModal();
    loadData();
  } catch (err) {
    modalErrorText = 'Erro ao salvar grupo: ' + err.message;
    renderModalBody();
  }
}

// Remove SÓ essa atividade do grupo (volta a mostrar com o rótulo próprio
// dela) — as outras atividades do grupo continuam juntas normalmente.
async function ungroupModalSelf() {
  if (!currentModalRow) return;
  try {
    await callApi(
      () => pywebview.api.set_group_override(currentModalRow.key, ''),
      '/api/set_group_override',
      { label_key: currentModalRow.key, group_name: '' }
    );
    closeSessionModal();
    loadData();
  } catch (err) {
    modalErrorText = 'Erro ao desagrupar: ' + err.message;
    renderModalBody();
  }
}

// Mesma coisa, mas pra outra atividade do grupo (vista na lista de membros),
// não a que está aberta na modal — não fecha a modal, só atualiza a lista.
async function ungroupModalMember(key) {
  try {
    await callApi(
      () => pywebview.api.set_group_override(key, ''),
      '/api/set_group_override',
      { label_key: key, group_name: '' }
    );
    await refreshModalGroupNames();
  } catch (err) {
    modalErrorText = 'Erro ao remover do grupo: ' + err.message;
    renderModalBody();
  }
}

async function deleteModalSessions() {
  if (!currentModalRow) return;
  const btn = document.getElementById('modal-delete-confirm-btn');
  const sessionIds = currentModalRow.items.map(i => i.id);
  if (btn) { btn.disabled = true; btn.textContent = 'Excluindo...'; }
  try {
    await callApi(
      () => pywebview.api.delete_sessions(sessionIds),
      '/api/delete_sessions',
      { session_ids: sessionIds }
    );
    if (btn) btn.textContent = 'Excluído!';
    await new Promise(r => setTimeout(r, 500));
    closeSessionModal();
    loadData();
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = 'Excluir'; }
    modalErrorText = 'Erro ao excluir: ' + err.message;
    renderModalBody();
  }
}

async function sendModalWorklog() {
  if (!currentModalRow) return;
  const statusEl = document.getElementById('modal-status');
  const code = document.getElementById('modal-send-code').value.trim();
  const raw = parseFloat(document.getElementById('modal-send-minutes').value.replace(',', '.'));
  const unit = document.getElementById('modal-send-unit').value;
  const minutes = unit === 'h' ? raw * 60 : raw;
  if (!code || !minutes || minutes <= 0) { statusEl.textContent = 'Preencha o código da issue e uma duração válida.'; return; }
  // Apontar de verdade integra com Jira/Tempo reais (precisa de credenciais
  // configuradas no app) — não tem um equivalente "de mentira" que faça
  // sentido testar fora do app instalado.
  if (typeof pywebview === 'undefined' || !pywebview.api) {
    statusEl.textContent = 'Apontar de verdade só funciona no app instalado (integra com Jira/Tempo reais).';
    return;
  }
  statusEl.textContent = 'Enviando...';
  const seconds = Math.round(minutes * 60);
  try {
    const r = await pywebview.api.send_worklog(currentModalRow.items.map(i => i.id), code, selectedDate, seconds, '');
    statusEl.textContent = r.ok ? 'Apontamento enviado ao Tempo!' : ('Erro: ' + r.error);
    if (r.ok) loadData();
  } catch (err) {
    statusEl.textContent = 'Erro: ' + err.message;
  }
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

  let html = `<div><div class="two-col-title">${title}</div>`;
  if (items.length === 0) {
    html += '<div style="color:var(--text-muted);font-size:12.5px;padding:6px 0">Nenhum registro</div>';
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
let calFilterTerm = '';

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
    // Dobrado (era 620) — a correção certa pra legibilidade é a ESCALA base
    // da tabela inteira, não forçar altura em cada bloco de evento via JS
    // (fizemos isso antes: um evento de 30min forçado pra 128px de altura
    // virava visualmente 5+ horas de bloco, porque 128px não tinha relação
    // nenhuma com a escala tempo→pixel real da grade — o evento aparecia
    // "no horário errado" porque continuava desenhado no topo certo, só que
    // enorme, invadindo as horas seguintes). Dobrar a altura total mantém a
    // proporção tempo→pixel correta em todo o calendário, então cada evento
    // cresce exatamente na medida certa pro tempo real que ele representa.
    // 24h * 65px — cada slot de 1h precisa caber pelo menos 2x a altura
    // mínima de um evento (32px), senão um evento no mínimo já estoura o
    // próprio slot visualmente.
    height: 1560,
    nowIndicator: true,
    allDaySlot: false,
    slotEventOverlap: false,
    // O seletor de dia acima do calendário já mostra "SEG 27/07" — o
    // cabeçalho de dia do próprio FullCalendar repetia a mesma informação.
    dayHeaders: false,
    eventOrder: (a, b) => (b.extendedProps.session?.total_seconds || 0) - (a.extendedProps.session?.total_seconds || 0),
    events: [],
    eventContent: renderCalendarEventContent,
    eventClick: (info) => {
      const g = info.event.extendedProps.session;
      if (g) renderSessionModal(g);
    },
    // Altura mínima pequena (16px, bem menor que a base de uma hora agora),
    // só pra eventos de segundos não colapsarem pra 1-2px — não tem risco
    // de "inflar" um evento curto pra parecer horas de duração, como o hack
    // de 128px antes causava.
    eventDidMount: (info) => {
      const harness = info.el.closest('.fc-timegrid-event-harness');
      if (!harness) return;
      // Harness no modo "-inset" do FullCalendar (classe aplicada quando
      // ele acha que tem empilhamento — fc-timegrid-event-harness-inset)
      // nunca seta uma altura explícita, usa "bottom" em vez disso — ler
      // harness.style.height sempre vinha vazio/0 pra esses casos, forçando
      // o clamp de altura mínima pra 32px em QUALQUER duração (um evento de
      // 43min saía do mesmo tamanho que um de 15min). Calcula a altura real
      // a partir da duração de verdade do evento, sem confiar no que o
      // FullCalendar colocou no harness.
      const totalMinutes = (info.event.end - info.event.start) / 60000;
      const pxPerMinute = 1560 / (24 * 60); // precisa bater com height: do Calendar acima
      const naturalHeight = totalMinutes * pxPerMinute;
      harness.style.height = Math.max(naturalHeight, 32) + 'px';
      harness.style.bottom = 'auto';
      // Ignora o left/right que o próprio FullCalendar calculou (tem casos
      // reais em que ele erra a divisão com vários eventos concorrentes) —
      // cada rótulo já tem sua coluna fixa decidida em updateWeekCalendar,
      // aplica direto. Não usa width: com left E right já setados, width é
      // ignorado pelo CSS (sistema sobre-restrito resolve por left/right).
      const { col, totalCols } = info.event.extendedProps;
      if (typeof col === 'number' && totalCols) {
        harness.style.left = (col * 100 / totalCols) + '%';
        harness.style.right = ((totalCols - col - 1) * 100 / totalCols) + '%';
      }
    },
  });
  weekCalendar.render();
  initEventHoverCard();
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
  const color = catColor(s.category);
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
  const label = s.displayLabel || s.groupLabel || s.detail || s.process || '';
  const wrap = document.createElement('div');
  wrap.className = 'fc-sess-event';
  wrap.style.borderLeft = `4px solid ${edgeColor}`;
  // Sem wrap.title — o tooltip nativo do navegador some depois de alguns
  // segundos e duplicaria o cartão flutuante (initEventHoverCard) mostrando
  // a mesma informação de dois jeitos diferentes ao mesmo tempo.
  wrap._chcSession = s;
  wrap._chcId = s.id || (s.process + s.category + s.detail + s.start);
  wrap.innerHTML = `<div class="fc-sess-bg" style="background:${color}"></div>${fgHtml}<div class="fc-sess-content"><span class="fc-sess-label">${esc(label)}</span></div>`;
  return { domNodes: [wrap] };
}

function groupSessionsByLabel(sessions) {
  // Uma sessão "crua" nasce toda vez que a captura perde e reencontra a
  // mesma janela — em vez de um evento por sessão crua (fragmentado),
  // agrupamos por rótulo (padrão: processo+categoria+detalhe, ou o nome de
  // grupo personalizado que o usuário deu) e desenhamos UMA barra só.
  const groups = new Map();
  for (const s of sessions) {
    const rawKey = s.process + '::' + s.category + '::' + s.detail;
    const displayKey = s.group_label || rawKey;
    if (!groups.has(displayKey)) {
      groups.set(displayKey, {
        key: rawKey, groupLabel: s.group_label || null,
        process: s.process, category: s.category, detail: s.detail,
        start: s.start, end: s.end,
        total_seconds: 0, foreground_seconds: 0, foreground_ranges: [],
        code: null, items: [],
      });
    }
    const g = groups.get(displayKey);
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
    g.total = g.total_seconds;
    // Categoria "app" é genérica — o detail é o título bruto da janela
    // (ex: "Aplicativos", um nome de pasta) e sozinho não diz qual app é.
    // As outras categorias já têm detail específico (nome da reunião,
    // pessoa do chat, página do navegador), então não precisam do prefixo.
    g.displayLabel = g.groupLabel || (g.category === 'app' && g.process && g.detail
      ? `${g.process} — ${g.detail}`
      : (g.detail || g.process));
  }
  return Array.from(groups.values());
}

function applyCalFilter() {
  calFilterTerm = (document.getElementById('cal-filter-input').value || '').trim().toLowerCase();
  updateWeekCalendar();
}

function updateWeekCalendar() {
  if (!weekCalendar) return;
  // Os dados recarregam sozinhos a cada 15s — o FullCalendar recria os
  // elementos de evento nesse processo, então o estado de hover rastreado
  // (referências antigas) precisa ser zerado junto.
  _hoveredHour = null;
  const events = [];
  const day = allData[selectedDate];
  if (day && day.sessions) {
    // Cada rótulo distinto tem sua PRÓPRIA coluna reservada pro dia inteiro
    // (00:00–23:59) — não é reaproveitada por outro rótulo em nenhum
    // momento, nem quando ela está "livre" no meio do dia. É a coluna
    // invisível: o mesmo rótulo pode voltar a aparecer nela mais tarde, mas
    // um rótulo DIFERENTE nunca entra ali.
    const rawGroups = groupSessionsByLabel(day.sessions).filter(g => {
      const label = (g.groupLabel || g.detail || g.process || '').toLowerCase();
      return !calFilterTerm || label.includes(calFilterTerm);
    });

    // Funde grupos que têm chaves internas diferentes mas o MESMO nome
    // visível (ex.: "Activity Tracker" capturado com processo/detalhe bruto
    // levemente diferente entre uma captura e outra, por instabilidade da
    // enumeração de janelas) — sem isso, cada variação virava um fragmento
    // picado à parte E podia abrir uma raia nova só pra si. O nome é a
    // identidade de verdade pro calendário; a chave fina (g.key) só importa
    // pra atribuir código/agrupamento manual ao clicar num bloco específico.
    const byName = new Map();
    for (const g of rawGroups) {
      // Normaliza (espaço solto, maiúscula/minúscula) — a diferença que
      // fragmenta na captura costuma ser exatamente esse tipo de ruído
      // invisível a olho nu, não uma mudança real de nome.
      const nameKey = (g.displayLabel || '').trim().toLowerCase().replace(/\s+/g, ' ');
      const existing = byName.get(nameKey);
      if (existing) {
        existing.items.push(...g.items);
        if (g.start < existing.start) existing.start = g.start;
        if (g.end > existing.end) existing.end = g.end;
      } else {
        byName.set(nameKey, { ...g, items: [...g.items] });
      }
    }
    const visibleGroups = [...byName.values()];
    visibleGroups.sort((a, b) => a.start.localeCompare(b.start));
    const totalCols = visibleGroups.length || 1;

    visibleGroups.forEach((g, col) => {
      // g.start/g.end cobrem a primeira até a última ocorrência do rótulo NO
      // DIA INTEIRO — usar isso direto faria um bloco só "engolir" o dia
      // todo. Cada aglomerado de ocorrências próximas (mesmo critério de
      // continuidade do motor de captura) vira seu próprio bloco, na
      // posição real em que aconteceu, mas sempre na MESMA coluna do rótulo.
      for (const c of clusterOccurrences(g.items, 15)) {
        events.push({
          start: c.start, end: c.end,
          extendedProps: {
            session: {
              ...g,
              start: c.start, end: c.end,
              total_seconds: c.total_seconds,
              foreground_seconds: c.foreground_seconds,
              foreground_ranges: c.items.flatMap(i => i.foreground_ranges || []),
              items: c.items,
            },
            col, totalCols,
          },
        });
      }
    });
  }
  weekCalendar.removeAllEventSources();
  weekCalendar.addEventSource(events);
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
  if (selectedDate !== todayStr) return;
  // Só a hora atual — nada de hora anterior/seguinte junto.
  const t = String(new Date().getHours()).padStart(2, '0') + ':00:00';
  el.querySelectorAll(`[data-time="${t}"]`).forEach(n => n.classList.add('cal-hour-focus'));
}

// Cartão flutuante com o texto completo ao passar o mouse num evento —
// blocos curtos empilhados na mesma hora não têm pixel vertical pra mostrar
// o rótulo sem cortar. Não tenta redimensionar a grade do FullCalendar: os
// eventos são posicionados por pixel numa camada própria
// (.fc-timegrid-col-events), desacoplada da tabela de fundo — confirmado
// depurando que crescer a linha da hora nunca fazia o bloco do evento
// acompanhar (só a grade invisível atrás dele crescia). O cartão é anexado
// direto no <body>, position:fixed, então escapa de qualquer
// overflow:hidden dos containers de scroll internos do FullCalendar.
//
// Só destaca a cor de fundo da hora sob o cursor (funciona em cima de
// evento OU de área vazia — detecção por posição do cursor contra o
// retângulo de cada linha, não por estar dentro de um elemento de evento).
// Não tenta redimensionar nada — nem a célula real do FullCalendar (provou
// ser instável com expandRows:true) nem o bloco do evento.
let _hoveredHour = null;
let _hourRowRects = null;

function _refreshHourRowRects(el) {
  const seen = new Set();
  const rects = [];
  el.querySelectorAll('.fc-timegrid-slot-lane[data-time]').forEach((n) => {
    const t = n.getAttribute('data-time');
    if (seen.has(t)) return;
    seen.add(t);
    const r = n.getBoundingClientRect();
    rects.push({ t, top: r.top, bottom: r.bottom });
  });
  _hourRowRects = rects;
}

function initHourRowHover(el) {
  el.addEventListener('mousemove', (e) => {
    _refreshHourRowRects(el);
    const hit = _hourRowRects.find((r) => e.clientY >= r.top && e.clientY < r.bottom);
    const t = hit ? hit.t : null;
    if (t === _hoveredHour) return;
    if (_hoveredHour !== null) {
      el.querySelectorAll(`[data-time="${_hoveredHour}"]`).forEach(n => n.classList.remove('cal-hour-hover'));
    }
    _hoveredHour = t;
    if (t !== null) {
      el.querySelectorAll(`[data-time="${t}"]`).forEach(n => n.classList.add('cal-hour-hover'));
    }
  });
  el.addEventListener('mouseleave', () => {
    if (_hoveredHour !== null) {
      el.querySelectorAll(`[data-time="${_hoveredHour}"]`).forEach(n => n.classList.remove('cal-hour-hover'));
    }
    _hoveredHour = null;
  });
}

function initEventHoverCard() {
  const el = document.getElementById('week-calendar');
  if (!el) return;
  initHourRowHover(el);
}

// ── Views (sidebar) ─────────────────────────────────────────────────────────
function toggleSidebar() {
  document.querySelector('.sidebar').classList.toggle('sidebar-closed');
}

const VIEWS = ['cal', 'res', 'cfg'];
function showView(name) {
  document.getElementById('view-cal').classList.toggle('hidden', name !== 'cal');
  document.getElementById('view-res').classList.toggle('hidden', name !== 'res');
  document.getElementById('view-cfg').classList.toggle('hidden', name !== 'cfg');
  document.querySelectorAll('.side-item').forEach((el, i) => el.classList.toggle('active', VIEWS[i] === name));
  // Seletor de dia só faz sentido em telas com dado de um dia específico.
  const showDayNav = name !== 'cfg';
  document.getElementById('daynav-wrap').classList.toggle('hidden', !showDayNav);
  if (name === 'cfg') loadSettingsData();
}
function openSettingsFromBanner() { showView('cfg'); }

// ── Pausar/retomar captura ───────────────────────────────────────────────────
const PAUSE_ICON = '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>';
const PLAY_ICON = '<polygon points="5 3 19 12 5 21 5 3"/>';
let capturePaused = false;
function applyCaptureState(paused) {
  capturePaused = paused;
  document.getElementById('label-pause-toggle').textContent = paused ? 'Retomar captura' : 'Pausar captura';
  document.getElementById('icon-pause-toggle').innerHTML = paused ? PLAY_ICON : PAUSE_ICON;
  document.getElementById('status-banner-manual').classList.toggle('hidden', !paused);
}
async function toggleCapture() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  const r = await pywebview.api.set_capture_paused(!capturePaused);
  applyCaptureState(r.paused);
}
async function initCaptureState() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  const r = await pywebview.api.get_capture_state();
  applyCaptureState(r.paused);
}

// ── Tema ──────────────────────────────────────────────────────────────────
function setTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode === 'light' ? 'light' : '');
  document.getElementById('theme-btn-dark').classList.toggle('active', mode === 'dark');
  document.getElementById('theme-btn-light').classList.toggle('active', mode === 'light');
  if (typeof pywebview !== 'undefined' && pywebview.api) pywebview.api.save_setting('theme', mode);
}
async function initTheme() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  const s = await pywebview.api.get_settings();
  if (s.theme === 'light') setTheme('light');
}

// Inicialização: aguarda pywebview estar pronto, ou inicia direto no navegador
let _appStarted = false;
function _startApp() {
  if (_appStarted) return;
  _appStarted = true;
  initWeekCalendar();
  loadData();
  initTheme();
  initCaptureState();
  setInterval(loadData, 15000);
}
window.addEventListener('pywebviewready', _startApp);
setTimeout(_startApp, 300);

// ── Configurações ─────────────────────────────────────────────────────────
let _bgEnabled = false;
let _loginEnabled = false;

function toggleJiraForm() {
  document.getElementById('jira-form-box').classList.toggle('hidden');
}

function renderJiraAccountStatus(jc) {
  const el = document.getElementById('jira-account-status');
  if (!el) return;
  const connected = !!(jc && jc.has_jira_token && jc.has_tempo_token && jc.base_url);
  el.innerHTML = connected
    ? `${esc(jc.email || jc.base_url)} <span class="status-pill ok" style="margin-left:6px;"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9 12l2 2 4-4"/></svg> Conectado</span>`
    : 'Nenhuma conta conectada';
}

async function loadSettingsData() {
  // Os toggles e a integração Jira mexem em segredo local do app instalado
  // (login automático, tokens) — sem equivalente de teste fora dele. A
  // lista de apps ignorados já tem fallback HTTP (refreshIgnoredChips), então
  // continua funcionando mesmo sem a ponte do pywebview.
  if (typeof pywebview !== 'undefined' && pywebview.api) {
    const s = await pywebview.api.get_settings();
    _bgEnabled = s.background_mode || false;
    _loginEnabled = s.login_mode || false;
    document.getElementById('tog-bg').classList.toggle('on', _bgEnabled);
    document.getElementById('tog-login').classList.toggle('on', _loginEnabled);
    const dd = document.getElementById('settings-data-dir');
    if (dd && s.data_dir) dd.textContent = s.data_dir;
    const jc = await pywebview.api.get_jira_config();
    document.getElementById('jira-url').value = jc.base_url || '';
    document.getElementById('jira-email').value = jc.email || '';
    document.getElementById('jira-token').placeholder = jc.has_jira_token ? 'Token salvo (deixe em branco p/ manter)' : 'API token do Jira';
    document.getElementById('tempo-token').placeholder = jc.has_tempo_token ? 'Token salvo (deixe em branco p/ manter)' : 'API token do Tempo';
    renderJiraAccountStatus(jc);
  }
  await refreshIgnoredChips();
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
  const jc = await pywebview.api.get_jira_config();
  renderJiraAccountStatus(jc);
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
  try {
    ignoredProcessesList = typeof pywebview !== 'undefined' && pywebview.api
      ? await pywebview.api.get_ignored_processes()
      : await (await fetch('/api/ignored_processes')).json();
    renderIgnoredChips();
  } catch (err) { /* não crítico pra carregar a tela */ }
}

function renderIgnoredChips() {
  const el = document.getElementById('ignored-chips');
  if (!el) return;
  if (!ignoredProcessesList || ignoredProcessesList.length === 0) {
    el.innerHTML = '<span style="color:var(--text-muted);font-size:12.5px;">Nenhum app ignorado</span>';
    return;
  }
  el.innerHTML = ignoredProcessesList.map(name => `
    <span class="ignored-chip">${esc(name)}<button onclick="removeIgnoredProcess('${esc(name).replace(/'/g, "\\'")}')" title="Voltar a rastrear">&#10005;</button></span>
  `).join('');
}

async function addIgnoredProcess() {
  const input = document.getElementById('ignored-new');
  const name = input.value.trim();
  if (!name) return;
  try {
    await callApi(
      () => pywebview.api.add_ignored_process(name),
      '/api/set_ignored_process',
      { name, ignored: true }
    );
    input.value = '';
    await refreshIgnoredChips();
  } catch (err) { /* poderia mostrar erro aqui se precisar depurar */ }
}

async function removeIgnoredProcess(name) {
  try {
    await callApi(
      () => pywebview.api.remove_ignored_process(name),
      '/api/set_ignored_process',
      { name, ignored: false }
    );
    await refreshIgnoredChips();
  } catch (err) { /* idem */ }
}
async function toggleBackground() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  _bgEnabled = !_bgEnabled;
  document.getElementById('tog-bg').classList.toggle('on', _bgEnabled);
  await pywebview.api.save_setting('background_mode', _bgEnabled);
}
async function toggleLogin() {
  if (typeof pywebview === 'undefined' || !pywebview.api) return;
  _loginEnabled = !_loginEnabled;
  document.getElementById('tog-login').classList.toggle('on', _loginEnabled);
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

        elif path == "/api/group_overrides":
            self._send_json(200, load_group_overrides())

        elif path == "/api/ignored_processes":
            import tracker
            self._send_json(200, sorted(tracker.get_ignored_processes()))

        else:
            self._send(404, "text/plain", b"Not found")

    # Ações que mudam estado (excluir, agrupar, código, rastrear/parar) só
    # tinham caminho via API do pywebview — no navegador comum (sem a ponte
    # nativa), os botões da modal ficavam sem efeito nenhum, silenciosamente,
    # porque é assim que o guard "typeof pywebview === 'undefined'" foi
    # desenhado pra se comportar. Esses endpoints dão um caminho HTTP
    # equivalente, mesmo padrão do /export/* já existente, pra essas ações
    # funcionarem de verdade também fora do app empacotado.
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._send_json(400, {"ok": False, "error": "JSON inválido"})
            return

        try:
            if path == "/api/delete_sessions":
                delete_sessions(payload.get("session_ids") or [])
                self._send_json(200, {"ok": True})

            elif path == "/api/set_group_override":
                set_group_override(payload.get("label_key") or "", (payload.get("group_name") or "").strip())
                self._send_json(200, {"ok": True})

            elif path == "/api/assign_jira_code":
                session_ids = payload.get("session_ids") or []
                code = payload.get("code") or ""
                if payload.get("apply_to_label") and payload.get("label_key"):
                    set_jira_label_code(payload["label_key"], code)
                else:
                    assign_jira_code(session_ids, code)
                self._send_json(200, {"ok": True})

            elif path == "/api/set_ignored_process":
                import tracker
                name = (payload.get("name") or "").strip()
                if name:
                    current = tracker.get_ignored_processes()
                    if payload.get("ignored"):
                        current.add(name.lower())
                    else:
                        current.discard(name.lower())
                    tracker.set_ignored_processes(sorted(current))
                self._send_json(200, {"ok": True})

            else:
                self._send(404, "text/plain", b"Not found")
        except Exception as e:
            self._send_json(500, {"ok": False, "error": str(e)})

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, "application/json; charset=utf-8", body)

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
