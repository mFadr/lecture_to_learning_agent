@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Nejprve spustte install.bat
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m lecture_to_learning
