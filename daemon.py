"""
Activity Tracker — Daemon silencioso.

Roda o tracker e os lembretes em segundo plano, sem abrir nenhuma janela.
Inicie este script para rastrear atividades; abra o dashboard separadamente.

Uso:
  Mac/Linux:  python3 daemon.py
  Windows:    pythonw daemon.py   (sem console)  ou  python daemon.py
"""

import sys
import time
import threading
from pathlib import Path

# Garante que imports relativos funcionem ao chamar de qualquer diretório
sys.path.insert(0, str(Path(__file__).parent))

from reminder import start_reminder_thread
from tracker import main as tracker_main


def main():
    start_reminder_thread()
    try:
        tracker_main()          # bloqueia; termina só com Ctrl+C / kill
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
