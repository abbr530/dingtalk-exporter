@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   钉钉聊天记录导出工具 - 安装脚本
echo ========================================
echo.

:: Check Python
echo [1/3] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    echo 安装时请勾选 "Add Python to PATH"
    pause
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do (
    echo   已找到 Python %%v
)

:: Install dependencies
echo.
echo [2/3] 安装 Python 依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络连接
    pause
    exit /b 1
)
echo   依赖安装完成

:: Check dingwave
echo.
echo [3/3] 检查 dingwave 解密工具...
if exist "tools\dingwave.exe" (
    echo   已找到 tools\dingwave.exe
) else (
    echo   [警告] 未找到 tools\dingwave.exe
    echo   请从 https://github.com/p1g3/dingwave/releases 下载
    echo   下载 dingwave_windows_amd64.zip，解压后将 dingwave.exe 放入 tools\ 目录
)

echo.
echo ========================================
echo   安装完成！
echo ========================================
echo.
echo 运行以下命令启动工具:
echo   python main.py
echo.
echo 然后浏览器访问 http://localhost:8090
echo.
pause
