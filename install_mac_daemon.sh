#!/usr/bin/env bash
# Activity Tracker — Instala daemon em background no macOS
# O daemon iniciará automaticamente no login e ficará sempre rodando.
#
# Uso: ./install_mac_daemon.sh
# Para desinstalar: ./install_mac_daemon.sh --uninstall

set -e

LABEL="com.activitytracker.daemon"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"
LOG_OUT="/tmp/activity-tracker.log"
LOG_ERR="/tmp/activity-tracker-error.log"

# ── Desinstalar ────────────────────────────────────────────────────────────────
if [[ "$1" == "--uninstall" ]]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "[OK] Daemon removido. O tracker nao iniciara mais automaticamente."
    exit 0
fi

# ── Instalar ───────────────────────────────────────────────────────────────────
if [[ ! -f "$PYTHON" ]]; then
    echo "[ERRO] python3 nao encontrado. Instale via: brew install python"
    exit 1
fi

echo "[INFO] Instalando daemon do Activity Tracker..."
echo "[INFO] Python: $PYTHON"
echo "[INFO] Scripts: $SCRIPT_DIR"

# Garante que psutil esta instalado (usado pelo lock file)
"$PYTHON" -c "import psutil" 2>/dev/null || "$PYTHON" -m pip install psutil --quiet

# Para instancia anterior se existir
launchctl unload "$PLIST" 2>/dev/null || true

# Gera o plist
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT_DIR/daemon.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_OUT</string>
    <key>StandardErrorPath</key>
    <string>$LOG_ERR</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF

# Carrega imediatamente
launchctl load "$PLIST"

echo ""
echo "[OK] Daemon instalado e iniciado!"
echo "[OK] O tracker rodara automaticamente a cada login."
echo ""
echo "Comandos uteis:"
echo "  Ver log:      tail -f $LOG_OUT"
echo "  Ver erros:    tail -f $LOG_ERR"
echo "  Parar agora:  launchctl unload \"$PLIST\""
echo "  Desinstalar:  ./install_mac_daemon.sh --uninstall"
echo ""
echo "Para abrir o dashboard: python3 start.py"
