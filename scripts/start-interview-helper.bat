@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 面试练习助手 - 启动器

rem 脚本位于 <项目根目录>\scripts 下，自动推导项目根目录，克隆到任意路径都能用。
for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
set "PY=%PROJECT_DIR%\backend\.venv\Scripts\python.exe"

echo ============================================
echo            面试练习助手 启动器
echo ============================================
echo.

if not exist "%PROJECT_DIR%" (
    echo [错误] 找不到项目目录：%PROJECT_DIR%
    echo        如果项目已经移动位置，请用记事本打开本脚本，
    echo        修改第 6 行的 PROJECT_DIR 后重试。
    goto :fail
)

if not exist "%PY%" (
    echo [错误] 找不到后端虚拟环境：%PY%
    echo        请先在 backend 目录执行：python -m venv .venv
    goto :fail
)

if not exist "%PROJECT_DIR%\frontend\node_modules" (
    echo [错误] 前端依赖未安装。
    echo        请先在 frontend 目录执行：npm install
    goto :fail
)

echo [1/4] 清理可能残留的旧进程...
for %%P in (%BACKEND_PORT% %FRONTEND_PORT%) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:"LISTENING" ^| findstr /R /C:":%%P "') do (
        echo       关闭占用端口 %%P 的进程 PID=%%A
        taskkill /F /PID %%A >nul 2>&1
    )
)
ping -n 3 127.0.0.1 >nul

echo [2/4] 启动后端服务 ^(端口 %BACKEND_PORT%^)...
start "面试助手-后端" /min cmd /c "cd /d "%PROJECT_DIR%\backend" && "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%"

echo [3/4] 等待后端就绪...
set "BACKEND_OK="
for /l %%i in (1,1,40) do (
    if not defined BACKEND_OK (
        ping -n 2 127.0.0.1 >nul
        netstat -ano | findstr /R /C:":%BACKEND_PORT% " | findstr /R /C:"LISTENING" >nul 2>&1
        if !errorlevel! equ 0 set "BACKEND_OK=1"
    )
)
if not defined BACKEND_OK (
    echo [警告] 后端在 40 秒内没有就绪，请查看「面试助手-后端」窗口的报错信息。
) else (
    echo       后端已就绪。
)

echo [4/4] 启动前端服务 ^(端口 %FRONTEND_PORT%^)...
start "面试助手-前端" /min cmd /c "cd /d "%PROJECT_DIR%\frontend" && npm run dev"

set "FRONTEND_OK="
for /l %%i in (1,1,40) do (
    if not defined FRONTEND_OK (
        ping -n 2 127.0.0.1 >nul
        netstat -ano | findstr /R /C:":%FRONTEND_PORT% " | findstr /R /C:"LISTENING" >nul 2>&1
        if !errorlevel! equ 0 set "FRONTEND_OK=1"
    )
)

echo.
if not defined FRONTEND_OK (
    echo [警告] 前端在 40 秒内没有就绪，请查看「面试助手-前端」窗口的报错信息。
    echo.
    goto :fail
)

echo ============================================
echo   启动完成
echo   访问地址：http://127.0.0.1:%FRONTEND_PORT%/
echo.
echo   两个黑色窗口是后端和前端服务，
echo   使用期间请勿关闭。用完后运行桌面的
echo   「停止面试练习助手」即可退出。
echo ============================================
echo.

start "" "http://127.0.0.1:%FRONTEND_PORT%/"
echo 浏览器已打开，本窗口 5 秒后自动关闭...
ping -n 6 127.0.0.1 >nul
exit /b 0

:fail
echo.
echo 启动失败，按任意键关闭本窗口。
pause >nul
exit /b 1
