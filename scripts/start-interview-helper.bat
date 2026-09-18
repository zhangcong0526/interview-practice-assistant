@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 面试练习助手 - 启动器

rem 脚本位于 <项目根目录>\scripts 下，自动推导项目根目录，克隆到任意路径都能用。
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
echo            面试练习助手 启动器
echo ============================================
echo.

if not exist "%PROJECT_DIR%\frontend\package.json" (
    echo [错误] 找不到项目文件，请确认脚本仍位于仓库的 scripts 目录中。
    goto :fail
)

if not exist "%REQUIREMENTS%" (
    echo [错误] 找不到后端依赖文件：%REQUIREMENTS%
    goto :fail
)

for /f "delims=" %%H in ('certutil -hashfile "%REQUIREMENTS%" SHA256 ^| find /v ":"') do (
    if not defined REQUIREMENTS_HASH set "REQUIREMENTS_HASH=%%H"
)
if exist "%DEPS_STAMP%" for /f "usebackq delims=" %%H in ("%DEPS_STAMP%") do (
    if not defined DEPS_STAMP_CONTENT set "DEPS_STAMP_CONTENT=%%H"
)

if not exist "%PACKAGE_LOCK%" (
    echo [错误] 找不到前端依赖锁文件：%PACKAGE_LOCK%
    goto :fail
)
for /f "delims=" %%H in ('certutil -hashfile "%PACKAGE_LOCK%" SHA256 ^| find /v ":"') do (
    if not defined PACKAGE_LOCK_HASH set "PACKAGE_LOCK_HASH=%%H"
)
if exist "%FRONTEND_STAMP%" for /f "usebackq delims=" %%H in ("%FRONTEND_STAMP%") do (
    if not defined FRONTEND_STAMP_CONTENT set "FRONTEND_STAMP_CONTENT=%%H"
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
    echo [错误] 未找到 Python 3.11+。
    echo        请安装 Python 并勾选 Add python.exe to PATH，然后重新运行本脚本。
    goto :fail
)
%PYTHON_CMD% %PYTHON_EXTRA_ARGS% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [错误] Python 版本过低，请安装 Python 3.11 或更高版本。
    goto :fail
)

node --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Node.js。请安装 Node.js 20+，安装后重新运行本脚本。
    goto :fail
)

npm --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 npm。请重新安装 Node.js 20+，安装后重新运行本脚本。
    goto :fail
)
set "NODE_MAJOR=0"
for /f "tokens=1 delims=." %%V in ('node --version') do set "NODE_MAJOR=%%V"
set "NODE_MAJOR=!NODE_MAJOR:v=!"
if !NODE_MAJOR! LSS 20 (
    echo [错误] Node.js 版本过低，请安装 Node.js 20 或更高版本。
    goto :fail
)

echo [1/5] 检查后端运行环境...
if not exist "%VENV_PY%" (
    echo       首次运行，创建 Python 虚拟环境...
    pushd "%BACKEND_DIR%" >nul
    %PYTHON_CMD% %PYTHON_EXTRA_ARGS% -m venv .venv
    if errorlevel 1 (
        popd >nul
        echo [错误] 创建虚拟环境失败。
        goto :fail
    )
    popd >nul
)

if not "%REQUIREMENTS_HASH%"=="%DEPS_STAMP_CONTENT%" (
    echo       安装或更新后端依赖，首次运行可能需要几分钟...
    "%VENV_PY%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo [错误] 升级 pip 失败，请检查网络或 Python 环境。
        goto :fail
    )
    "%VENV_PY%" -m pip install -r "%REQUIREMENTS%"
    if errorlevel 1 (
        echo [错误] 安装后端依赖失败。
        goto :fail
    )
    > "%DEPS_STAMP%" echo %REQUIREMENTS_HASH%
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [警告] 未找到 ffmpeg。页面可以启动，但音视频压缩和本地转写可能不可用。
)
where ffprobe >nul 2>&1
if errorlevel 1 (
    echo [警告] 未找到 ffprobe。请安装 FFmpeg 并确认它已加入 PATH。
)

echo [2/5] 检查环境变量配置...
if not exist "%ENV_EXAMPLE%" (
    echo [错误] 找不到后端环境变量模板：%ENV_EXAMPLE%
    goto :fail
)

if not exist "%ENV_FILE%" (
    copy /Y "%ENV_EXAMPLE%" "%ENV_FILE%" >nul
    if errorlevel 1 (
        echo [错误] 创建 backend\.env 失败。
        goto :fail
    )
    echo       已从 .env.example 创建 backend\.env。
    choice /c YN /n /m "是否现在用记事本填写 DEEPSEEK_API_KEY？[Y/N] "
    if errorlevel 2 goto env_ready
    if errorlevel 1 (
        start "编辑环境配置" notepad "%ENV_FILE%"
        echo.
        echo       请在记事本中填写 DEEPSEEK_API_KEY，保存后回到本窗口。
        pause
    )
)
:env_ready

set "HAS_DEEPSEEK_KEY="
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if /I "%%A"=="DEEPSEEK_API_KEY" if not "%%B"=="" set "HAS_DEEPSEEK_KEY=1"
)
if not defined HAS_DEEPSEEK_KEY (
    echo [警告] DEEPSEEK_API_KEY 尚未配置。页面可以打开，但 AI 分析、命题和模拟面试点评不可用。
    echo        配置位置：%ENV_FILE%
)

echo [3/5] 检查前端依赖...
if not "%PACKAGE_LOCK_HASH%"=="%FRONTEND_STAMP_CONTENT%" (
    echo       安装或更新前端依赖，可能需要几分钟...
    pushd "%FRONTEND_DIR%" >nul
    call npm ci
    if errorlevel 1 (
        popd >nul
        echo [错误] 安装前端依赖失败。可进入 frontend 目录执行 npm install 排查。
        goto :fail
    )
    popd >nul
    > "%FRONTEND_STAMP%" echo %PACKAGE_LOCK_HASH%
)

echo [4/5] 清理可能残留的旧进程并启动后端...
for %%P in (%BACKEND_PORT% %FRONTEND_PORT%) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:"LISTENING" ^| findstr /R /C:":%%P "') do (
        echo       关闭占用端口 %%P 的进程 PID=%%A
        taskkill /F /PID %%A >nul 2>&1
    )
)
ping -n 3 127.0.0.1 >nul

start "面试助手-后端" /D "%BACKEND_DIR%" /MIN cmd /c ""%VENV_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT% ^& pause"

echo       等待后端就绪...
set "BACKEND_OK="
for /l %%i in (1,1,45) do (
    if not defined BACKEND_OK (
        ping -n 2 127.0.0.1 >nul
        netstat -ano | findstr /R /C:":%BACKEND_PORT% " | findstr /R /C:"LISTENING" >nul 2>&1
        if !errorlevel! equ 0 set "BACKEND_OK=1"
    )
)
if not defined BACKEND_OK (
    echo [警告] 后端在 45 秒内没有就绪，请查看最小化的「面试助手-后端」窗口。
) else (
    echo       后端已就绪。
)

echo [5/5] 启动前端服务...
start "面试助手-前端" /D "%FRONTEND_DIR%" /MIN cmd /c "npm run dev ^& pause"

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
    echo [警告] 前端在 45 秒内没有就绪，请查看最小化的「面试助手-前端」窗口。
    echo.
    goto :fail
)

echo ============================================
echo   启动完成
echo   访问地址：http://127.0.0.1:%FRONTEND_PORT%/
echo.
echo   两个最小化窗口是后端和前端服务，使用期间请勿关闭。
echo   用完后双击项目根目录的 stop.bat 即可停止。
echo ============================================
echo.

start "" "http://127.0.0.1:%FRONTEND_PORT%/"
echo 浏览器已打开，本窗口 5 秒后自动关闭...
ping -n 6 127.0.0.1 >nul
exit /b 0

:fail
echo.
echo 启动未完成，按任意键关闭本窗口。
pause >nul
exit /b 1
