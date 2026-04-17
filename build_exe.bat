@echo off
echo Cerrando procesos existentes...
:: Matamos el proceso y sus hijos (/t) para liberar bloqueos
taskkill /f /t /im run_waitress.exe 2>nul
:: Esperamos unos segundos para que Windows libere los descriptores de archivo
timeout /t 3 /nobreak >nul

echo Limpiando carpetas de compilacion anteriores...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

if exist dist (
    echo ERROR: No se pudo eliminar la carpeta 'dist'. Asegurate de cerrar el Explorador de Archivos en esa ruta y que ningun programa este usando los archivos.
    pause
    exit /b
)

echo Iniciando compilacion con PyInstaller...
:: Usamos python -m para asegurar que use el interprete correcto
:: Se incluye --collect-all para asegurar dependencias de langchain/google
python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onedir ^
    --console ^
    --add-data "templates;templates" ^
    --add-data ".env;." ^
    --collect-all langchain_google_genai ^
    run_waitress.py

echo Proceso finalizado. El ejecutable se encuentra en dist/run_waitress/
pause