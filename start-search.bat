@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 scripts\searxng.py start
if errorlevel 1 pause
