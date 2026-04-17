import os

if __name__ == "__main__":
    # Aseguramos que el directorio de trabajo sea el del script para evitar problemas con SQLite
    basedir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(basedir)
    print(f"DEBUG: Directorio de trabajo establecido en: {os.getcwd()}", flush=True)

    # Importamos app y waitress DESPUÉS de asegurar el directorio de trabajo
    print("DEBUG: Cargando módulos de la aplicación...", flush=True)
    from app import app
    from waitress import serve
    
    print(f"Servidor listo y escuchando en http://127.0.0.1:8080", flush=True)
    # 'threads=4' es un buen punto de partida para una app interna
    serve(app, host='0.0.0.0', port=8080, threads=4)