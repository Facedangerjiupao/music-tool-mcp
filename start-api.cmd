@echo off
set PYTHONUTF8=1
set TRANSPORT_MODE=hybrid
set HOST=127.0.0.1
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0server.py"
