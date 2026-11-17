@echo off
setlocal
cd /d "%~dp0.."
echo === GAMING_AI_EDITOR : installation ===
set "PY=python"
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
%PY% --version >nul 2>nul
if errorlevel 1 goto :nopython

if exist ".venv\Scripts\python.exe" goto :deps
echo Creation de l'environnement Python local (.venv)...
%PY% -m venv .venv
if errorlevel 1 goto :fail

:deps
echo Installation des dependances (NumPy, OpenCV)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

where ffmpeg >nul 2>nul
if not errorlevel 1 goto :inspect
echo.
echo FFmpeg est introuvable sur ce PC.
choice /C ON /M "Installer FFmpeg maintenant avec winget (gratuit) ? O = oui, N = non"
if errorlevel 2 goto :inspect
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
echo.
echo FFmpeg vient d'etre installe. FERME cette fenetre, puis relance scripts\install.bat
echo pour que Windows le reconnaisse.
pause
exit /b 0

:inspect
echo.
".venv\Scripts\python.exe" -m gaming_ai_editor inspect
echo.
echo Installation terminee. Lance start.bat pour demarrer.
pause
exit /b 0

:nopython
echo Python est introuvable.
echo Installe Python 3.10 ou plus recent depuis https://www.python.org/downloads/
echo en cochant "Add python.exe to PATH", puis relance ce script.
pause
exit /b 1

:fail
echo.
echo L'installation a echoue. Lis le message d'erreur ci-dessus.
pause
exit /b 1
