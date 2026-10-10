@echo off
setlocal
cd /d "%~dp0"
python tools\start_demo.py %*
set "result=%errorlevel%"
if /I "%~1"=="--no-pause" goto done
pause
:done
exit /b %result%
