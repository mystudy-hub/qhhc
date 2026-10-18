@echo off
REM 启动 vnpy 图形界面（CTA 回测 / 数据管理）
setlocal
cd /d "%~dp0\.."
".venv\Scripts\python.exe" gui.py
pause
