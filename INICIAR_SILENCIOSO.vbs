' Activity Tracker — inicia o daemon em segundo plano (sem janela, sem console)
' Registre no Agendador de Tarefas via REGISTRAR_INICIO_AUTOMATICO.bat

Dim objShell
Set objShell = CreateObject("WScript.Shell")

Dim scriptDir
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))

' pythonw = Python sem janela de console
objShell.Run "pythonw """ & scriptDir & "daemon.py""", 0, False
