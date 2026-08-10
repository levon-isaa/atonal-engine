@echo off
REM One-time setup for Windows: venv + deps + model download (~300MB)
cd /d "%~dp0"
echo [1/3] creating virtual environment...
python -m venv .venv
call .venv\Scripts\activate.bat
echo [2/3] installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo [3/3] downloading AI tagging model (~300MB, one-time)...
python -c "import tagger; tagger.ensure_model(); print('model ready')"
echo.
echo Setup complete. Start it with:  run.bat   then open http://127.0.0.1:8770/
pause
