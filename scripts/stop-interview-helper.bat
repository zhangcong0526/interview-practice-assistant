@echo off
chcp 65001 >nul
title 面试练习助手 - 停止

set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"

echo ============================================
echo            停止面试练习助手
echo ============================================
echo.

set "FOUND="
for %%P in (%BACKEND_PORT% %FRONTEND_PORT%) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:"LISTENING" ^| findstr /R /C:":%%P "') do (
        echo 关闭端口 %%P 上的服务 ^(PID %%A^)
        taskkill /F /PID %%A >nul 2>&1
        set "FOUND=1"
    )
)

if not defined FOUND (
    echo 没有检测到正在运行的服务。
) else (
    echo.
    echo 服务已全部停止。
)

echo.
ping -n 4 127.0.0.1 >nul
exit /b 0
