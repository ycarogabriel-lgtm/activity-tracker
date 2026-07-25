"""
Integração com Jira + Tempo Timesheets para apontamento de horas.

Os tokens (segredos) ficam no cofre nativo do sistema operacional via
`keyring` (Keychain no macOS, Credential Manager no Windows) — nunca em
texto puro em disco. Só a URL do Jira e o e-mail (não sensíveis) ficam
num arquivo de config local.

Não depende do conector Atlassian/MCP: fala direto com a API REST do
Jira Cloud (autenticação básica e-mail + API token) e com a API do Tempo
(Bearer token), ambas por HTTP puro via `urllib`.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

KEYRING_SERVICE = "ActivityTracker"
KEYRING_JIRA_TOKEN = "jira_api_token"
KEYRING_TEMPO_TOKEN = "tempo_api_token"


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


CONFIG_FILE = _data_dir() / "jira_config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(base_url: str, email: str):
    cfg = load_config()
    cfg["base_url"] = (base_url or "").rstrip("/")
    cfg["email"] = email or ""
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _keyring():
    import keyring
    return keyring


def save_tokens(jira_token: str = None, tempo_token: str = None):
    kr = _keyring()
    if jira_token:
        kr.set_password(KEYRING_SERVICE, KEYRING_JIRA_TOKEN, jira_token)
    if tempo_token:
        kr.set_password(KEYRING_SERVICE, KEYRING_TEMPO_TOKEN, tempo_token)


def get_tokens() -> dict:
    try:
        kr = _keyring()
        return {
            "jira_token": kr.get_password(KEYRING_SERVICE, KEYRING_JIRA_TOKEN),
            "tempo_token": kr.get_password(KEYRING_SERVICE, KEYRING_TEMPO_TOKEN),
        }
    except Exception:
        return {"jira_token": None, "tempo_token": None}


def has_credentials() -> bool:
    cfg = load_config()
    tokens = get_tokens()
    return bool(cfg.get("base_url") and cfg.get("email") and tokens.get("jira_token") and tokens.get("tempo_token"))


def _jira_request(path: str, method="GET", body=None):
    cfg = load_config()
    tokens = get_tokens()
    base_url, email, token = cfg.get("base_url"), cfg.get("email"), tokens.get("jira_token")
    if not (base_url and email and token):
        raise RuntimeError("Configuração do Jira incompleta (URL, e-mail ou token faltando).")
    req = urllib.request.Request(f"{base_url}{path}", method=method)
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Jira retornou erro {e.code}: {e.read().decode(errors='ignore')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Falha de conexão com o Jira: {e.reason}")


def _tempo_request(path: str, method="GET", body=None):
    token = get_tokens().get("tempo_token")
    if not token:
        raise RuntimeError("Token do Tempo não configurado.")
    req = urllib.request.Request(f"https://api.tempo.io/4{path}", method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Tempo retornou erro {e.code}: {e.read().decode(errors='ignore')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Falha de conexão com o Tempo: {e.reason}")


def test_connection() -> dict:
    """Valida as credenciais do Jira e guarda o accountId (necessário pro Tempo)."""
    me = _jira_request("/rest/api/3/myself")
    account_id = me.get("accountId")
    cfg = load_config()
    cfg["account_id"] = account_id
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "display_name": me.get("displayName"), "account_id": account_id}


def resolve_issue_id(issue_key: str) -> str:
    data = _jira_request(f"/rest/api/3/issue/{issue_key}?fields=id")
    issue_id = data.get("id")
    if not issue_id:
        raise RuntimeError(f"Issue '{issue_key}' não encontrada.")
    return issue_id


def send_worklog(issue_key: str, date_str: str, seconds: int, description: str = "") -> dict:
    cfg = load_config()
    account_id = cfg.get("account_id")
    if not account_id:
        account_id = test_connection()["account_id"]
    issue_id = resolve_issue_id(issue_key)
    body = {
        "issueId": int(issue_id),
        "timeSpentSeconds": int(seconds),
        "startDate": date_str,
        "startTime": "09:00:00",
        "authorAccountId": account_id,
        "description": description or "Apontado via Activity Tracker",
    }
    return _tempo_request("/worklogs", method="POST", body=body)
