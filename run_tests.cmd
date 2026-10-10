@echo off
setlocal
cd /d "%~dp0"
python -m unittest discover -s tests -v
set "result=%errorlevel%"
if /I "%~1"=="--no-pause" goto done
pause
:done
exit /b %result%
