"""
Gera dados de demonstração para o painel web.
Execute apenas para testar o painel sem o rastreador real.
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LOG_FILE = SCRIPT_DIR / "activity_log.json"

SAMPLE_ACTIVITIES = [
    # (category, detail, process, weight)
    ("teams_meeting", "Daily Scrum - Equipe de Desenvolvimento", "ms-teams.exe", 3),
    ("teams_meeting", "Reunião de Planejamento Sprint", "ms-teams.exe", 2),
    ("teams_meeting", "Alinhamento com Cliente XYZ", "ms-teams.exe", 2),
    ("teams_meeting", "1:1 com Gerente", "ms-teams.exe", 1),
    ("teams_chat", "Maria Silva", "ms-teams.exe", 4),
    ("teams_chat", "João Pereira", "ms-teams.exe", 3),
    ("teams_chat", "Equipe de Projetos", "ms-teams.exe", 2),
    ("teams_chat", "Ana Costa", "ms-teams.exe", 2),
    ("browser", "Jira - Quadro do Sprint", "msedge.exe", 5),
    ("browser", "GitHub - Pull Requests", "msedge.exe", 4),
    ("browser", "Confluence - Documentação", "msedge.exe", 3),
    ("browser", "Gmail - Caixa de Entrada", "msedge.exe", 3),
    ("browser", "Stack Overflow", "chrome.exe", 2),
    ("app", "Visual Studio Code - projeto-api", "Code.exe", 6),
    ("app", "Microsoft Word - Relatório Mensal.docx", "WINWORD.EXE", 3),
    ("app", "Microsoft Excel - Planilha de Horas.xlsx", "EXCEL.EXE", 2),
    ("app", "Outlook - Caixa de Entrada", "OUTLOOK.EXE", 3),
    ("idle", "", "", 2),
]

def generate_day(base_date: datetime, num_records: int = 80):
    records = []
    current_time = base_date.replace(hour=8, minute=30, second=0)
    end_time = base_date.replace(hour=18, minute=30, second=0)

    weights = [a[3] for a in SAMPLE_ACTIVITIES]
    total_weight = sum(weights)
    probs = [w / total_weight for w in weights]

    while current_time < end_time and len(records) < num_records:
        # Escolhe atividade com peso
        r = random.random()
        cumulative = 0
        chosen = SAMPLE_ACTIVITIES[0]
        for act, prob in zip(SAMPLE_ACTIVITIES, probs):
            cumulative += prob
            if r <= cumulative:
                chosen = act
                break

        cat, detail, proc, _ = chosen
        duration = random.randint(30, 900)  # 30s a 15min

        record = {
            "timestamp": current_time.isoformat(timespec="seconds"),
            "date": current_time.strftime("%Y-%m-%d"),
            "time": current_time.strftime("%H:%M:%S"),
            "category": cat,
            "detail": detail,
            "process": proc,
            "title": detail,
            "teams_status": "Available",
            "teams_in_meeting_log": cat == "teams_meeting",
            "duration_seconds": duration,
        }
        records.append(record)
        current_time += timedelta(seconds=duration)

    return records


def main():
    all_records = []
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for days_ago in range(5):
        day = today - timedelta(days=days_ago)
        if day.weekday() < 5:  # Apenas dias úteis
            records = generate_day(day, num_records=random.randint(60, 100))
            all_records.extend(records)

    all_records.sort(key=lambda r: r["timestamp"])

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    print(f"[OK] {len(all_records)} registros de demonstração gerados em {LOG_FILE}")


if __name__ == "__main__":
    main()
