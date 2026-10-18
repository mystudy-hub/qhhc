@echo off
REM 下载期货行情数据到本地 vnpy 数据库
REM 用法: fetch_data.bat rb i TA au
setlocal
cd /d "%~dp0\.."

if "%~1"=="" (
    echo 用法: fetch_data.bat ^<品种代码...^>
    echo 示例: fetch_data.bat rb i TA au SA cu m
    echo 全部: fetch_data.bat --all
    echo 查看: fetch_data.bat --list
    pause
    exit /b 0
)

".venv\Scripts\python.exe" -m cta_lab.dataio %*
pause
