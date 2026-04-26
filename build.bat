@echo off
setlocal EnableDelayedExpansion

echo ================================================
echo  Track Manager — Build Script
echo ================================================

:: ── 1. PyInstaller: bundle del servidor Python ──────────────────────────────
echo.
echo [1/3] Compilando servidor Python con PyInstaller...
call pyinstaller server.spec --noconfirm --clean
if errorlevel 1 (
    echo ERROR: PyInstaller fallo.
    exit /b 1
)
echo OK: dist\server\ generado.

:: ── 2. Copiar bundle al directorio Tauri ────────────────────────────────────
echo.
echo [2/3] Copiando servidor al proyecto Tauri...
if exist "tauri-app\dist\server" rmdir /s /q "tauri-app\dist\server"
mkdir "tauri-app\dist"
xcopy /e /i /q "dist\server" "tauri-app\dist\server"
if errorlevel 1 (
    echo ERROR: No se pudo copiar el servidor.
    exit /b 1
)
echo OK: servidor copiado a tauri-app\dist\server\

:: ── 3. Tauri: build del instalador .exe ─────────────────────────────────────
echo.
echo [3/3] Compilando app Tauri...
cd tauri-app
call npm install
call npm run tauri build
if errorlevel 1 (
    echo ERROR: Tauri build fallo.
    cd ..
    exit /b 1
)
cd ..

echo.
echo ================================================
echo  Build completado!
echo  Instalador: tauri-app\src-tauri\target\release\bundle\nsis\
echo ================================================
