@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Environnement absent : lance d'abord scripts\install.bat
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import numpy, PIL" >nul 2>nul
if errorlevel 1 (
  echo Des modules manquent ^(NumPy ou Pillow^) : relance scripts\install.bat
  echo et lis le message d'erreur s'il y en a un.
  pause
  exit /b 1
)
if /i not "%~1"=="--no-browser" start "" "http://127.0.0.1:8765"
echo GAMING_AI_EDITOR demarre. Depose tes videos dans le dossier INPUT.
echo Laisse cette fenetre ouverte. Ctrl+C pour arreter.
".venv\Scripts\python.exe" -m gaming_ai_editor run
pause
