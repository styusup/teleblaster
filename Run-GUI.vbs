Option Explicit

' Silent launcher untuk Run-GUI.bat. Cocok dipakai sebagai shortcut di
' Desktop / taskbar supaya GUI jalan tanpa jendela CMD.
'
' Catatan:
' - Saat first run (venv belum ada / .env belum ada / dependencies belum
'   terinstall), launcher otomatis fall back ke Run-GUI.bat dengan jendela
'   CMD terlihat agar user bisa baca pesan & isi API_ID / API_HASH.
' - Begitu setup beres, klik file ini untuk run silent dengan pythonw.

Dim fso, shell, scriptDir, batPath, envPath, venvLocal, venvParent
Dim pywPath, cmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath  = scriptDir & "\Run-GUI.bat"
envPath  = scriptDir & "\.env"
venvLocal  = scriptDir & "\.venv\Scripts\pythonw.exe"
venvParent = fso.GetParentFolderName(scriptDir) & "\.venv\Scripts\pythonw.exe"

' Jika setup belum lengkap, jalankan .bat (visible) supaya user bisa interact.
If (Not fso.FileExists(envPath)) Or _
   ((Not fso.FileExists(venvLocal)) And (Not fso.FileExists(venvParent))) Then
    shell.Run """" & batPath & """", 1, False
    WScript.Quit
End If

' Setup beres -> run silent dengan pythonw langsung.
If fso.FileExists(venvLocal) Then
    pywPath = venvLocal
ElseIf fso.FileExists(venvParent) Then
    pywPath = venvParent
Else
    pywPath = "pythonw.exe"
End If

cmd = "cmd /c cd /d """ & scriptDir & """ && """ & pywPath & """ ""gui_app.py"""
shell.Run cmd, 0, False
