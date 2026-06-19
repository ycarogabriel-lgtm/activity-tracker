@echo off
title Activity Tracker
echo ============================================================
echo   Activity Tracker - Rastreador de Atividades
echo ============================================================
echo.

REM Verifica se Python está instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado. Instale em https://python.org
    pause
    exit /b 1
)

REM Instala dependencias se necessario
echo [INFO] Verificando dependencias...
pip install pywin32 psutil --quiet

echo [INFO] Iniciando rastreador e painel web...
echo [INFO] O painel abrira automaticamente em http://localhost:5000
echo [INFO] Pressione Ctrl+C para encerrar.
echo.

python "%~dp0start.py"

pause
