@echo off
setlocal

REM ============================================================
REM  run_app.bat -- Corre a CS2 Pulse (Streamlit)
REM  Mantem este ficheiro na pasta cs2_pulse (com app.py, etc.)
REM ============================================================

cd /d "%~dp0"

set CS2_PULSE_PASSWORD=pulse2026

echo.
echo === CS2 Pulse ===
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Nao encontrei o Python no PATH.
    echo Instala em https://www.python.org/downloads/ e marca
    echo "Add Python to PATH" durante a instalacao.
    pause
    exit /b 1
)

echo A verificar dependencias (so demora na 1a vez)...
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias.
    pause
    exit /b 1
)

echo.
echo A abrir a app no browser...
python -m streamlit run app.py

pause
