@echo off
setlocal
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call install.bat
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" scripts\launcher.py stop %*
if errorlevel 1 pause
