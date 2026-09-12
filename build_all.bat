@echo off
chcp 65001 >nul
title DSL TUI - Full Build Automation (with UPX compression)

REM ============================================================
REM Full build: dsl_tui_app + dbnocode_init + server + fbserver.
REM Every PyInstaller step uses UPX when available so the EXEs
REM are smaller. Set AUTOB_BUILD=1 to skip the final pause.
REM
REM UPX is downloaded automatically on first run (needs network).
REM If offline, the build still succeeds - just without compression.
REM ============================================================

echo ==================================================
echo Step 1: Installing required packages...
echo ==================================================
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo Package installation failed!
    pause
    exit /b 1
)
echo All packages installed successfully.
echo.

echo ==================================================
echo Step 2: Setting up UPX compression...
echo ==================================================
set UPX_DIR=
if exist "%~dp0upx\upx.exe" set UPX_DIR=%~dp0upx
if not defined UPX_DIR (
    echo Downloading UPX 4.2.4 for smaller EXEs...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-win64.zip' -OutFile '%TEMP%\upx.zip'" >nul 2>&1
    if exist "%TEMP%\upx.zip" (
        powershell -Command "Expand-Archive -Path '%TEMP%\upx.zip' -DestinationPath '%TEMP%\upx_extract' -Force" >nul 2>&1
        for /r "%TEMP%\upx_extract" %%f in (upx.exe) do copy /y "%%f" "%~dp0upx\" >nul 2>&1
        if exist "%~dp0upx\upx.exe" set UPX_DIR=%~dp0upx
    )
)
if defined UPX_DIR (
    echo Using UPX at %UPX_DIR%
    set UPX_ARG=--upx-dir=%UPX_DIR%
) else (
    echo UPX not available - building without compression, larger EXEs.
    set UPX_ARG=
)
echo.
REM Drop heavy scientific/ML packages that are installed in the environment
REM but not used by the TUI runtime (they bloat the bundle enormously).
set EXCLUDES=--exclude-module torch --exclude-module torchaudio --exclude-module tensorflow --exclude-module scipy --exclude-module numba --exclude-module sympy --exclude-module pyarrow

echo ==================================================
echo Step 3: Cleaning old build files...
echo ==================================================
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.spec del /q *.spec
if exist final_gui.py del /q final_gui.py
echo Clean complete.
echo.

echo ==================================================
echo Step 4: Building DSL runner EXE...
echo ==================================================
pyinstaller --onefile --clean --name dsl_tui_app %UPX_ARG% %EXCLUDES% --add-data "dsl_lib;dsl_lib" --add-data "tui;tui" --add-data "scripts;scripts" main.py
if %errorlevel% neq 0 (
    echo Runner build failed!
    pause
    exit /b 1
)
echo Runner EXE built successfully.
echo.

echo ==================================================
echo Step 5: Building distributable initializer EXE...
echo ==================================================
echo Bundling runtime exe into initializer data...
pyinstaller --onefile --clean --name dbnocode_init %UPX_ARG% %EXCLUDES% ^
    --add-data "main.py;." ^
    --add-data "dist\dsl_tui_app.exe;." ^
    --add-data "dsl_lib;dsl_lib" ^
    --add-data "tui;tui" ^
    --add-data "docs;docs" ^
    --add-data "scripts;scripts" ^
    init_app.py
if %errorlevel% neq 0 (
    echo Initializer build failed!
    pause
    exit /b 1
)
echo Initializer EXE built successfully.
echo.

echo ==================================================
echo Step 6: Building SQL server EXE...
echo ==================================================
set SRV_INCLUDES=--hidden-import=websockets --hidden-import=websockets.asyncio --hidden-import=websockets.asyncio.server --hidden-import=websockets.headers --hidden-import=websockets.uri --hidden-import=websockets.frames --hidden-import=websockets.http11 --hidden-import=websockets.legacy --hidden-import=websockets.streams --hidden-import=websockets.sync --hidden-import=websockets.typing --hidden-import=websockets.version
python -c "import fdb" >nul 2>&1 && set SRV_INCLUDES=%SRV_INCLUDES% --hidden-import=fdb --hidden-import=fdb.fbcore
python -c "import psycopg2" >nul 2>&1 && set SRV_INCLUDES=%SRV_INCLUDES% --hidden-import=psycopg2 --hidden-import=psycopg2.extensions --hidden-import=psycopg2.extras --hidden-import=psycopg2.sql
pyinstaller --onefile --clean --name dbnocode-server %UPX_ARG% %SRV_INCLUDES% server.py
if %errorlevel% neq 0 (
    echo Server build failed!
    pause
    exit /b 1
)
echo Server EXE built successfully.
echo.

echo ==================================================
echo Step 7: Building Firebird-only server EXE...
echo ==================================================
pyinstaller --onefile --clean --name dbnocode-fbserver %UPX_ARG% %SRV_INCLUDES% FBServer.py
if %errorlevel% neq 0 (
    echo Firebird server build failed!
    pause
    exit /b 1
)
echo Firebird server EXE built successfully.
echo.

echo ==================================================
echo ALL BUILDS COMPLETE!
echo ==================================================
echo Output files:
echo   - dist\dsl_tui_app.exe       runtime
echo   - dist\dbnocode_init.exe     distribute to end users
echo   - dist\dbnocode-server.exe   SQL server, all backends
echo   - dist\dbnocode-fbserver.exe SQL server, Firebird only
echo.
if not "%AUTOB_BUILD%"=="1" pause
exit /b 0
