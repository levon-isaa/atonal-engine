@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
set "_P=%ATONAL_PORT%"
if "%_P%"=="" set "_P=8770"
echo ATONAL running -> open http://127.0.0.1:%_P%/   (Ctrl-C to stop)
python server.py
