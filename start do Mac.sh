#!/usr/bin/env bash
# Activity Tracker — Inicializador para macOS
# Uso: ./start.sh   (ou duplo clique no Finder com permissão de execução)

cd "$(dirname "$0")"

# Garante que psutil está instalado (único requisito no macOS)
python3 -c "import psutil" 2>/dev/null || python3 -m pip install psutil --quiet

# Inicia o tracker
python3 start.py
