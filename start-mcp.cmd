@echo off
set PYTHONUTF8=1
set TRANSPORT_MODE=stdio
"%~dp0.venv\Scripts\python.exe" "%~dp0server.py"
