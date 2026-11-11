@echo off
setlocal
cd /d "%~dp0"
python main.py
if errorlevel 1 (
    echo.
    echo No se pudo iniciar. Revisa el error mostrado arriba.
    pause
)
