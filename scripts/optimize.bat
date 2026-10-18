@echo off
REM 参数网格寻优
REM 用法: optimize.bat rb --strategy MaCross
setlocal
cd /d "%~dp0\.."

if "%~1"=="" (
    echo 用法: optimize.bat ^<品种^> [--strategy 策略名] [--metric 目标指标]
    echo 示例: optimize.bat rb --strategy Donchian --metric sharpe_ratio
    pause
    exit /b 0
)

".venv\Scripts\python.exe" -m cta_lab.cli optimize %*
pause
