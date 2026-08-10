#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate
echo "ATONAL running -> open http://127.0.0.1:8770/   (Ctrl-C to stop)"
python server.py
