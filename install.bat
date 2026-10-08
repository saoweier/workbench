@echo off
setlocal
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Please install Python 3.13 and enable Add Python to PATH.
  pause
  exit /b 1
)
python scripts\bootstrap.py %*
if errorlevel 1 (
  pause
  exit /b 1
)
