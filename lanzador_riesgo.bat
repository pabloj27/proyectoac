@echo off
:: Cambia al directorio donde se encuentra este archivo .bat
cd /d "%~dp0"

echo Iniciando Sistema de Analisis de Riesgo...
:: Abre el navegador predeterminado en la direccion local
start http://127.0.0.1:8080
:: Ejecuta el servidor usando Waitress
python run_waitress.py
pause