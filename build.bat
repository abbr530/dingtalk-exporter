@echo off
echo ========================================
echo  Building DingTalk Exporter as EXE
echo ========================================
echo.

REM Check if pyinstaller is installed
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
    if %errorlevel% neq 0 (
        echo Failed to install PyInstaller
        pause
        exit /b 1
    )
)

echo Building with PyInstaller...
echo This may take a few minutes...
echo.

pyinstaller --clean --noconfirm dingtalk-exporter.spec

if %errorlevel% neq 0 (
    echo.
    echo Build failed! Check the output above for errors.
    pause
    exit /b 1
)

echo.
echo ========================================
echo  Build completed successfully!
echo ========================================
echo.
echo Output: dist\dingtalk-exporter\dingtalk-exporter.exe
echo   and:   dist\dingtalk-exporter\dingtalk-mcp.exe
echo.
echo To run: Double-click dist\dingtalk-exporter\dingtalk-exporter.exe
echo   or:   dist\dingtalk-exporter\dingtalk-exporter.exe
echo.
echo MCP: Register dist\dingtalk-exporter\dingtalk-mcp.exe as a stdio
echo      MCP server in Claude Desktop / Cursor / WorkBuddy etc.
echo.
echo Note: The tools\ directory should be next to the EXE
echo       for the dingwave voice decoder to work.
echo.
pause
