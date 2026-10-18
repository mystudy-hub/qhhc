@echo off
REM ===================================================================
REM  期货 CTA 回测环境 —— 安装脚本（Windows）
REM  双击运行，或在 cmd 中执行 setup.bat
REM ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo [1/3] 检查 Python ...
python --version >nul 2>&1
if errorlevel 1 (
    echo   错误: 未找到 python。请先安装 Python 3.10+ 并加入 PATH。
    echo   下载: https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   Python %PYVER%

echo.
echo [2/3] 创建虚拟环境 .venv ...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 (
        echo   虚拟环境创建失败。
        pause
        exit /b 1
    )
    echo   已创建。
) else (
    echo   已存在，跳过。
)

echo.
echo [3/3] 安装依赖（vnpy 4.5 + 官方 App，约 300MB，首次需几分钟）...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   依赖安装失败。可尝试国内镜像：
    echo     .venv\Scripts\python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

echo.
echo 安装完成，开始环境自检 ...
echo.
".venv\Scripts\python.exe" check_env.py

echo.
echo ===================================================================
echo  下一步：
echo    scripts\fetch_data.bat rb i TA au      下载行情数据
echo    scripts\backtest.bat rb                回测螺纹钢
echo ===================================================================
pause
