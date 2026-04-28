@echo off
echo === Track Manager Agent — Build ===

set VENV=..\.venv-build\Scripts

echo [1/3] Instalando dependencias...
%VENV%\pip install mutagen --quiet
%VENV%\pip install deemix --quiet 2>nul || echo [INFO] deemix no disponible, Deezer no estara disponible

echo [2/3] Buildeando .exe...
%VENV%\pyinstaller ^
  --onefile ^
  --noconsole ^
  --name TrackManagerAgent ^
  --hidden-import httpx ^
  --hidden-import mutagen.mp3 ^
  --add-data "download;download" ^
  agent.py

if %errorlevel% neq 0 (
  echo BUILD FALLIDO.
  pause
  exit /b 1
)

echo [3/3] Copiando a static...
copy /Y dist\TrackManagerAgent.exe ..\app\static\agent\TrackManagerAgent.exe

echo.
echo === Build exitoso! ===
pause
