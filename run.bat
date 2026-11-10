@echo off
setlocal
cd /d "%~dp0"

echo [AutoMemoryGuard] Starting application...
python main.py %*

endlocal
