@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo ATONAL running -> open http://127.0.0.1:8770/   (Ctrl-C to stop)
python server.py
