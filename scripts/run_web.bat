@echo off
REM 启动 TradingView 风格 Web 图表
setlocal
cd /d "%~dp0\.."

echo.
echo   启动 CTA Lab Chart ...
echo   浏览器将自动打开 http://127.0.0.1:8765
echo   按 Ctrl+C 停止服务
echo.

".venv\Scripts\python.exe" -m cta_lab.web.server %*
pause
