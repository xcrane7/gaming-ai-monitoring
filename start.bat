@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Environnement absent : lance d'abord scripts\install.bat
  pause
  exit /b 1
)
if /i not "%~1"=="--no-browser" start "" "http://127.0.0.1:8765"
echo GAMING_AI_EDITOR demarre. Depose tes videos dans le dossier INPUT.
echo Laisse cette fenetre ouverte. Ctrl+C pour arreter.
".venv\Scripts\python.exe" -m gaming_ai_editor run
pause
