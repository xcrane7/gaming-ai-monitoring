@echo off
rem Optionnel : sous-titres automatiques locaux et gratuits (faster-whisper).
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m pip install faster-whisper
pause
