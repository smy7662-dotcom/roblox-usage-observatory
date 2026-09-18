@echo off
rem Roblox observatory: wake the GitHub collector only when a run was missed.
rem ASCII only + CRLF (see memory: scheduled task launcher encoding).
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"
"C:\Users\mdymr\AppData\Local\Programs\Python\Python312\python.exe" kick_if_due.py
exit /b %ERRORLEVEL%
