@echo off
chcp 65001 >nul
title DSL No-Code TUI — Install and Run
cd /d "%~dp0"

echo ==================================================
echo  Step 1: Checking Python...
echo ==================================================
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python not found. Please install Python 3.8+ and add it to PATH.
    pause
    exit /b 1
)
python --version
echo.

echo ==================================================
echo  Step 2: Installing required packages...
echo ==================================================
pip install --upgrade pip -q
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo Package installation failed!
    pause
    exit /b 1
)
echo All packages installed.
echo.

echo ==================================================
echo  Step 3: Validating sample scripts...
echo ==================================================
python main.py scripts\01_main_menu.dsl --validate-only
python main.py scripts\02_membership.dsl --validate-only
echo.

echo ==================================================
echo  Select a sample to run:
echo ==================================================
echo  1. Main Menu Demo       (scripts\01_main_menu.dsl)
echo  2. Membership System    (scripts\02_membership.dsl)
echo  Q. Quit
echo.
set /p choice="Enter choice (1/2/Q): "

if /i "%choice%"=="1" (
    echo.
    echo Launching Main Menu Demo...
    python main.py scripts\01_main_menu.dsl
) else if /i "%choice%"=="2" (
    echo.
    echo Launching Membership System...
    python main.py scripts\02_membership.dsl
) else if /i "%choice%"=="Q" (
    echo Bye.
    exit /b 0
) else (
    echo Invalid choice.
)

echo.
pause
exit /b 0
