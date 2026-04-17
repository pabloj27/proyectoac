import webview
import threading
import os
from waitress import serve
from app import app

def start_server():
    basedir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(basedir)
    serve(app, host='127.0.0.1', port=8080, threads=4)

if __name__ == "__main__":
    # Iniciamos Waitress en un hilo separado
    t = threading.Thread(target=start_server)
    t.daemon = True
    t.start()

    # Creamos la ventana de la aplicacion
    webview.create_window('Analisis de Riesgo - Fianzas y Credito', 'http://127.0.0.1:8080', width=1200, height=800)
    webview.start()