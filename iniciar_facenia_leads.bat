@echo off
chcp 65001 > nul
title FACENIA Lead Finder - Buscador de Clientes Potenciales
color 0B

echo ======================================================================
echo             FACENIA Lead Finder — Iniciando Aplicacion
echo ======================================================================
echo.

:: Cambiar al directorio donde se encuentra este archivo .bat
cd /d "%~dp0"

:: 1. Verificar si existe el entorno virtual .venv
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creando entorno virtual .venv...
    py -3.13 -m venv .venv 2>nul
    if errorlevel 1 (
        py -3.10 -m venv .venv 2>nul
        if errorlevel 1 (
            python -m venv .venv
        )
    )
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual. Asegurate de tener Python instalado.
        pause
        exit /b 1
    )
    echo [OK] Entorno virtual creado.
    echo.
    echo [INFO] Instalando dependencias necesarias (esto solo ocurre la primera vez)...
    .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
    echo [OK] Dependencias instaladas correctamente.
    echo.
)

:: 2. Verificar archivo .env
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env > nul
        echo [INFO] Archivo .env inicializado.
        echo [INFO] La aplicacion utiliza OpenStreetMap como fuente gratuita principal.
        echo [INFO] No es necesario introducir ninguna tarjeta ni clave de pago.
        echo.
    )
)

:: 3. Lanzar Streamlit en el navegador
echo [INFO] Iniciando servidor local de FACENIA Lead Finder...
echo [INFO] La interfaz se abrira automaticamente en tu navegador web.
echo.
echo Presiona Ctrl + C en esta ventana cuando desees detener la aplicacion.
echo ----------------------------------------------------------------------

.\.venv\Scripts\python.exe -m streamlit run app.py --server.headless=false --browser.gatherUsageStats=false

if errorlevel 1 (
    echo.
    echo [ERROR] Ocurrio un problema al ejecutar la aplicacion.
    pause
)
