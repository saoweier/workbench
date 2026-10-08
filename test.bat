@echo off
REM Content Workbench regression tests (each stage uses isolated temporary data).
setlocal
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo X 未找到虚拟环境，先跑 install.bat
  pause
  exit /b 1
)
set "VPY=%CD%\.venv\Scripts\python.exe"
"%VPY%" -B scripts\run_all_tests.py %*
if not %errorlevel%==0 (
  pause
  exit /b 1
)
pause
