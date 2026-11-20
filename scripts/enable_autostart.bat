@echo off
rem Lance GAMING_AI_EDITOR a chaque ouverture de session Windows (fenetre reduite).
rem Le Skills Researcher reprend ainsi tout seul apres un arret du PC.
setlocal
cd /d "%~dp0.."
set "ROOT=%cd%"
schtasks /Create /F /TN "GamingAIEditor" /SC ONLOGON /RL LIMITED /TR "cmd /c start \"GamingAIEditor\" /min \"%ROOT%\start.bat\" --no-browser"
if errorlevel 1 (
  echo Echec de la creation de la tache planifiee.
) else (
  echo Demarrage automatique active. Pour le retirer : scripts\disable_autostart.bat
)
pause
