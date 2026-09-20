@echo off
cd /d %~dp0
if not exist .venv\Scripts\uvicorn.exe (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
