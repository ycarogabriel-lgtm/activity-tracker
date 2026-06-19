' Inicia o Activity Tracker sem mostrar janela do terminal
' Use este arquivo se preferir rodar em segundo plano

Dim objShell
Set objShell = CreateObject("WScript.Shell")

' Pega o diretório do script
Dim scriptDir
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))

' Inicia o tracker em background (sem janela)
objShell.Run "python """ & scriptDir & "tracker.py""", 0, False

' Aguarda 2 segundos
WScript.Sleep 2000

' Inicia o servidor web em background
objShell.Run "python """ & scriptDir & "server.py""", 0, False

' Aguarda mais 2 segundos e abre o navegador
WScript.Sleep 2000
objShell.Run "http://localhost:5000", 1, False

WScript.Echo "Activity Tracker iniciado! Acesse http://localhost:5000 no navegador."
