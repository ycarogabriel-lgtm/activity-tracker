@echo off
title Registrar Activity Tracker no Agendador de Tarefas
echo ============================================================
echo   Activity Tracker - Configurar Inicio Automatico
echo ============================================================
echo.

REM Obtém o diretório atual do script
set "SCRIPT_DIR=%~dp0"
REM Remove a barra final
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "VBS_PATH=%SCRIPT_DIR%\INICIAR_SILENCIOSO.vbs"
set "TASK_NAME=ActivityTracker"

REM Verifica se o arquivo VBS existe
if not exist "%VBS_PATH%" (
    echo [ERRO] Arquivo nao encontrado: %VBS_PATH%
    echo        Certifique-se de que INICIAR_SILENCIOSO.vbs esta na mesma pasta.
    pause
    exit /b 1
)

echo [INFO] Registrando tarefa no Agendador do Windows...
echo [INFO] Pasta do tracker: %SCRIPT_DIR%
echo [INFO] Arquivo de inicio: %VBS_PATH%
echo.

REM Remove tarefa existente com o mesmo nome (se houver)
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM Cria a tarefa agendada:
REM   - Disparada no logon do usuário atual
REM   - Roda wscript.exe com o VBS silencioso
REM   - Sem janela visível (WSCRIPT já garante isso pelo VBS)
REM   - Atraso de 30 segundos para garantir que o ambiente esteja pronto
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "wscript.exe \"%VBS_PATH%\"" ^
    /sc onlogon ^
    /delay 0000:30 ^
    /ru "%USERNAME%" ^
    /rl limited ^
    /f

if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao criar a tarefa. Tente executar este script como Administrador.
    pause
    exit /b 1
)

echo.
echo [OK] Tarefa "%TASK_NAME%" criada com sucesso!
echo [OK] O Activity Tracker iniciara automaticamente no proximo login.
echo.
echo Para verificar: abra o Agendador de Tarefas e procure por "%TASK_NAME%"
echo Para remover:   schtasks /delete /tn "%TASK_NAME%" /f
echo.
pause
