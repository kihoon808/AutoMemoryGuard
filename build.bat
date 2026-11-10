@echo off
setlocal
cd /d "%~dp0"

echo [AutoMemoryGuard] Starting PyInstaller build process...
python build_exe.py

if %ERRORLEVEL% equ 0 (
    echo [AutoMemoryGuard] Build succeeded. Executable is located in dist\AutoMemoryGuard.exe
) else (
    echo [AutoMemoryGuard] Build failed with error code %ERRORLEVEL%.
)

endlocal
