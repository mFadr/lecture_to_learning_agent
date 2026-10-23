@echo off
rem Volitelné sestavení samostatné aplikace (složka dist\LectureToLearning) pomocí PyInstaller.
rem Lokální modely Ollamy NEJSOU součástí balíčku – instalují se zvlášť (ollama pull ...).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install "pyinstaller>=6.6" || exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --windowed --name LectureToLearning ^
    --collect-submodules lecture_to_learning ^
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia ^
    launcher.py || exit /b 1
echo Hotovo: dist\LectureToLearning\LectureToLearning.exe
