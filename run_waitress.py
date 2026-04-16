import os
from waitress import serve
from app import app

if __name__ == "__main__":
    # Aseguramos que el directorio de trabajo sea el del script para evitar problemas con SQLite
    basedir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(basedir)
    
    print(f"Servidor iniciado en http://0.0.0.0:8080")
    # 'threads=4' es un buen punto de partida para una app interna
    serve(app, host='0.0.0.0', port=8080, threads=4)