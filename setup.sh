#!/usr/bin/env bash
# One-time setup: creates a venv, installs deps, downloads the ML model (~300MB).
set -e
cd "$(dirname "$0")"
echo "[1/3] creating virtual environment…"
python3 -m venv .venv
source .venv/bin/activate
echo "[2/3] installing Python dependencies (this can take a few minutes)…"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt
echo "[3/3] downloading the AI tagging model (~300MB, one-time)…"
python -c "import tagger; tagger.ensure_model(); print('model ready')"
echo
echo "Setup complete.  Start it with:   ./run.sh    then open http://127.0.0.1:8770/"
