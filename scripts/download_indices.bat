@echo off
REM 下载中国期货全品种加权指数到本地 vnpy 数据库
REM 用法: download_indices.bat [--interval d|1h|1m]
setlocal
cd /d "%~dp0\.."

".venv\Scripts\python.exe" scripts\download_indices.py %*
pause
