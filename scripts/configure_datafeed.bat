@echo off
REM 配置 / 验证 vnpy 历史数据服务（天勤 TqSdk）
setlocal
cd /d "%~dp0\.."

if "%~1"=="" (
    echo 用法:
    echo   configure_datafeed.bat show
    echo   configure_datafeed.bat set ^<快期账号^> ^<密码^>
    echo   configure_datafeed.bat verify
    echo   configure_datafeed.bat clear
    echo.
    echo 天勤账号免费注册: https://account.shinnytech.com/
    pause
    exit /b 0
)

if /i "%~1"=="show" (
    ".venv\Scripts\python.exe" -m cta_lab.datafeed_setup --show
) else if /i "%~1"=="verify" (
    ".venv\Scripts\python.exe" -m cta_lab.datafeed_setup --verify
) else if /i "%~1"=="clear" (
    ".venv\Scripts\python.exe" -m cta_lab.datafeed_setup --clear
) else if /i "%~1"=="set" (
    ".venv\Scripts\python.exe" -m cta_lab.datafeed_setup --username "%~2" --password "%~3" --verify
) else (
    echo 未知参数: %~1
)
pause
