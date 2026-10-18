@echo off
REM 单品种回测
REM 用法: backtest.bat rb
REM       backtest.bat rb --strategy Donchian
REM       backtest.bat au --strategy AtrChannel --start 2015-01-01
setlocal
cd /d "%~dp0\.."

if "%~1"=="" (
    echo 用法: backtest.bat ^<品种^> [选项]
    echo.
    echo 示例:
    echo   backtest.bat rb
    echo   backtest.bat rb --strategy Donchian
    echo   backtest.bat au --strategy AtrChannel --param atr_multiplier=2.0
    echo   backtest.bat i  --strategy MaCross --param fast_window=5 --param slow_window=60
    echo.
    echo 可用策略: MaCross / Donchian / AtrChannel / BollingerRevert
    echo           也可用 vnpy 官方(裸名): doublema bollchannel atrrsi turtle dualtrust kingkeltner
    pause
    exit /b 0
)

".venv\Scripts\python.exe" -m cta_lab.cli backtest %*
pause
