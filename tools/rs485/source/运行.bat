@echo off
rem ============================================================
rem  ASCII only in this file. Chinese menu lives in tools\menu.py
rem  (cmd.exe parses .bat with the system ANSI codepage, UTF-8
rem   Chinese would break command lines.)
rem ============================================================
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found in this folder.
    echo         Create it first:  python -m venv .venv
    echo         Then install deps: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "tools\menu.py"

echo.
echo ============================================================
echo    Closed. Press any key to exit.
echo ============================================================
pause >nul
