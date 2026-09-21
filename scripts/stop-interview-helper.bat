@echo off
title Interview Practice Assistant - Stop

set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"

echo ============================================
echo        Stop Interview Practice Assistant
echo ============================================
echo.

set "FOUND="
for %%P in (%BACKEND_PORT% %FRONTEND_PORT%) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:"LISTENING" ^| findstr /R /C:":%%P "') do (
        echo Stopping service on port %%P ^(PID %%A^)
        taskkill /F /PID %%A >nul 2>&1
        set "FOUND=1"
    )
)

if not defined FOUND (
    echo No running services detected.
) else (
    echo.
    echo All services stopped.
)

echo.
ping -n 4 127.0.0.1 >nul
exit /b 0
