@echo off
setlocal enabledelayedexpansion
title Interview Practice Assistant - Launcher

rem This script infers the project root so it works from any clone path.
for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
set "BACKEND_DIR=%PROJECT_DIR%\backend"
set "FRONTEND_DIR=%PROJECT_DIR%\frontend"
set "VENV_PY=%BACKEND_DIR%\.venv\Scripts\python.exe"
set "ENV_FILE=%BACKEND_DIR%\.env"
set "ENV_EXAMPLE=%BACKEND_DIR%\.env.example"
set "REQUIREMENTS=%BACKEND_DIR%\requirements.txt"
set "DEPS_STAMP=%BACKEND_DIR%\.venv\.requirements-installed"
set "PACKAGE_LOCK=%FRONTEND_DIR%\package-lock.json"
set "FRONTEND_STAMP=%FRONTEND_DIR%\node_modules\.package-lock-installed"
set "PYTHON_EXTRA_ARGS="
set "REQUIREMENTS_HASH="
set "DEPS_STAMP_CONTENT="
set "PACKAGE_LOCK_HASH="
set "FRONTEND_STAMP_CONTENT="

echo ============================================
echo        Interview Practice Assistant Launcher
echo ============================================
echo.
if not exist "%PROJECT_DIR%\frontend\package.json" (
    echo [ERROR] Project file not found. Keep this script inside the repository scripts directory.
    goto :fail
)
if not exist "%REQUIREMENTS%" (
    echo [ERROR] Backend requirements not found: %REQUIREMENTS%
    goto :fail
)

for /f "delims=" %%H in ('certutil -hashfile "%REQUIREMENTS%" SHA256 ^| find /v ":"') do (
    if not defined REQUIREMENTS_HASH set "REQUIREMENTS_HASH=%%H"
)
if exist "%DEPS_STAMP%" (
    for /f "usebackq delims=" %%H in ("%DEPS_STAMP%") do (
    if not defined DEPS_STAMP_CONTENT set "DEPS_STAMP_CONTENT=%%H"
    )
)

if not exist "%PACKAGE_LOCK%" (
    echo [ERROR] Frontend package lock not found: %PACKAGE_LOCK%
    goto :fail
)
for /f "delims=" %%H in ('certutil -hashfile "%PACKAGE_LOCK%" SHA256 ^| find /v ":"') do (
    if not defined PACKAGE_LOCK_HASH set "PACKAGE_LOCK_HASH=%%H"
)
if exist "%FRONTEND_STAMP%" (
    for /f "usebackq delims=" %%H in ("%FRONTEND_STAMP%") do (
    if not defined FRONTEND_STAMP_CONTENT set "FRONTEND_STAMP_CONTENT=%%H"
    )
)

set "PYTHON_CMD="
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
) else (
    py -3 --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py"
        set "PYTHON_EXTRA_ARGS=-3"
    )
)

if not defined PYTHON_CMD (
    echo [ERROR] Python not found.
    echo        Install Python and select "Add python.exe to PATH".
    goto :fail
)
%PYTHON_CMD% %PYTHON_EXTRA_ARGS% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11 or newer is required.
    goto :fail
)

node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Install Node.js 20 or newer.
    goto :fail
)

call npm --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm not found. Reinstall Node.js 20 or newer.
    goto :fail
)
set "NODE_MAJOR=0"
for /f "tokens=1 delims=." %%V in ('node --version') do set "NODE_MAJOR=%%V"
set "NODE_MAJOR=!NODE_MAJOR:v=!"
if !NODE_MAJOR! LSS 20 (
    echo [ERROR] Node.js 20 or newer is required.
    goto :fail
)

echo [1/5] Checking backend environment...
if not exist "%VENV_PY%" (
    echo       First run: creating Python virtual environment...
    pushd "%BACKEND_DIR%" >nul
    %PYTHON_CMD% %PYTHON_EXTRA_ARGS% -m venv .venv
    if errorlevel 1 (
        popd >nul
        echo [ERROR] Failed to create Python virtual environment.
        goto :fail
    )
    popd >nul
)

if not "%REQUIREMENTS_HASH%"=="%DEPS_STAMP_CONTENT%" (
    echo       Installing or updating backend dependencies. First run may take several minutes.
    "%VENV_PY%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo [ERROR] Failed to upgrade pip. Check network or Python environment.
        goto :fail
    )
    "%VENV_PY%" -m pip install -r "%REQUIREMENTS%"
    if errorlevel 1 (
        echo [ERROR] Failed to install backend dependencies.
        goto :fail
    )
    > "%DEPS_STAMP%" echo %REQUIREMENTS_HASH%
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [WARN] ffmpeg not found. Audio/video compression and local transcription may be unavailable.
)
where ffprobe >nul 2>&1
if errorlevel 1 (
    echo [WARN] ffprobe not found. Install FFmpeg and add it to PATH.
)

echo [2/5] Checking environment configuration...
if not exist "%ENV_EXAMPLE%" (
    echo [ERROR] Backend environment template not found: %ENV_EXAMPLE%
    goto :fail
)

if not exist "%ENV_FILE%" (
    copy /Y "%ENV_EXAMPLE%" "%ENV_FILE%" >nul
    if errorlevel 1 (
        echo [ERROR] Failed to create backend\.env.
        goto :fail
    )
    echo       Created backend\.env from .env.example.
    choice /c YN /n /m "Open DEEPSEEK_API_KEY in Notepad now? [Y/N] "
    if errorlevel 2 goto env_ready
    if errorlevel 1 (
        start "Edit environment" notepad "%ENV_FILE%"
        echo.
        echo       Fill in DEEPSEEK_API_KEY, save the file, and return to this window.
        pause
    )
)
:env_ready

set "ACTIVE_LLM_PROVIDER=deepseek"
if defined LLM_PROVIDER set "ACTIVE_LLM_PROVIDER=%LLM_PROVIDER%"
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if /I "%%A"=="LLM_PROVIDER" set "ACTIVE_LLM_PROVIDER=%%B"
)
set "HAS_ACTIVE_KEY="
if /I "!ACTIVE_LLM_PROVIDER!"=="deepseek" (
    if defined DEEPSEEK_API_KEY set "HAS_ACTIVE_KEY=1"
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /I "%%A"=="DEEPSEEK_API_KEY" if not "%%B"=="" set "HAS_ACTIVE_KEY=1"
    )
) else if /I "!ACTIVE_LLM_PROVIDER!"=="ark" (
    if defined ARK_API_KEY set "HAS_ACTIVE_KEY=1"
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /I "%%A"=="ARK_API_KEY" if not "%%B"=="" set "HAS_ACTIVE_KEY=1"
    )
) else if /I "!ACTIVE_LLM_PROVIDER!"=="minimax" (
    if defined MINIMAX_API_KEY set "HAS_ACTIVE_KEY=1"
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /I "%%A"=="MINIMAX_API_KEY" if not "%%B"=="" set "HAS_ACTIVE_KEY=1"
    )
) else if /I "!ACTIVE_LLM_PROVIDER!"=="openai" (
    if defined OPENAI_API_KEY set "HAS_ACTIVE_KEY=1"
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /I "%%A"=="OPENAI_API_KEY" if not "%%B"=="" set "HAS_ACTIVE_KEY=1"
    )
)
if not defined HAS_ACTIVE_KEY (
    echo [WARN] No API key configured for active LLM provider: !ACTIVE_LLM_PROVIDER!
    echo        Configure DeepSeek, Volcengine Ark, MiniMax, or OpenAI in the UI header.
    echo        Configuration file: %ENV_FILE%
)

echo [3/5] Checking frontend dependencies...
if not "%PACKAGE_LOCK_HASH%"=="%FRONTEND_STAMP_CONTENT%" (
    echo       Installing or updating frontend dependencies. This may take several minutes.
    pushd "%FRONTEND_DIR%" >nul
    call npm ci
    if errorlevel 1 (
        popd >nul
        echo [ERROR] Failed to install frontend dependencies. Run npm install in the frontend directory to debug.
        goto :fail
    )
    popd >nul
    > "%FRONTEND_STAMP%" echo %PACKAGE_LOCK_HASH%
)

echo [4/5] Cleaning stale services and starting backend...
for %%P in (%BACKEND_PORT% %FRONTEND_PORT%) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:"LISTENING" ^| findstr /R /C:":%%P "') do (
        echo       Stopping process PID=%%A on port %%P
        taskkill /F /PID %%A >nul 2>&1
    )
)
ping -n 3 127.0.0.1 >nul

start "Interview Assistant Backend" /D "%BACKEND_DIR%" /MIN "%VENV_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%

echo       Waiting for backend...
set "BACKEND_OK="
for /l %%i in (1,1,45) do (
    if not defined BACKEND_OK (
        ping -n 2 127.0.0.1 >nul
        netstat -ano | findstr /R /C:":%BACKEND_PORT% " | findstr /R /C:"LISTENING" >nul 2>&1
        if !errorlevel! equ 0 set "BACKEND_OK=1"
    )
)
if not defined BACKEND_OK (
    echo [WARN] Backend did not become ready within 45 seconds. Check the minimized backend window.
    goto :fail
) else (
    echo       Backend is ready.
)

echo [5/5] Starting frontend...
start "Interview Assistant Frontend" /D "%FRONTEND_DIR%" /MIN cmd /c "npm run dev ^& pause"

set "FRONTEND_OK="
for /l %%i in (1,1,45) do (
    if not defined FRONTEND_OK (
        ping -n 2 127.0.0.1 >nul
        netstat -ano | findstr /R /C:":%FRONTEND_PORT% " | findstr /R /C:"LISTENING" >nul 2>&1
        if !errorlevel! equ 0 set "FRONTEND_OK=1"
    )
)

echo.
if not defined FRONTEND_OK (
    echo [WARN] Frontend did not become ready within 45 seconds. Check the minimized frontend window.
    echo.
    goto :fail
)

echo ============================================
echo   Startup complete
echo   URL: http://127.0.0.1:%FRONTEND_PORT%/
echo.
echo   Keep the two minimized service windows open while using the app.
echo   Run stop.bat in the repository root when finished.
echo ============================================
echo.

start "" "http://127.0.0.1:%FRONTEND_PORT%/"
echo Browser opened. This window closes in 5 seconds...
ping -n 6 127.0.0.1 >nul
exit /b 0

:fail
echo.
echo Startup failed. Press any key to close this window.
pause >nul
exit /b 1
