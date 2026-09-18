@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call "%~dp0scripts\start-interview-helper.bat"
exit /b %errorlevel%
