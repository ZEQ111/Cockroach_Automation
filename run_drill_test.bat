@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ===============================================
echo  Drill D1-D3 console
echo ===============================================
python code\pc\drill_console.py
echo.
pause
