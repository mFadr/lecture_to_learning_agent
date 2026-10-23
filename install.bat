@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === Lecture-to-Learning Agent: instalace ===

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python nebyl nalezen. Nainstalujte Python 3.12 nebo 3.13 z https://www.python.org/downloads/
        echo a pri instalaci zaskrtnete "Add python.exe to PATH".
        pause
        exit /b 1
    )
    set "PY=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo Vytvarim virtualni prostredi .venv ...
    %PY% -m venv .venv || goto :error
)
echo Instaluji zavislosti (muze trvat nekolik minut) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error

echo.
echo Hotovo. Aplikaci spustite souborem run.bat
echo Pro lokalni rezim nainstalujte Ollamu (https://ollama.com) a stahnete model, napr.:
echo     ollama pull llama3.1:8b
pause
exit /b 0

:error
echo Instalace selhala – viz vypis vyse.
pause
exit /b 1
