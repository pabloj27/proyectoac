# migrate_data.py
import sys
import os
from datetime import date

# Añadir el directorio actual al path para que pueda encontrar app y Base
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from Base import EECCDB, EmpresaDB, session
from app import compute_metrics, calculate_scoring, parse_date # Importa las funciones necesarias

def migrate_existing_balances():
    print("Iniciando migración de datos para balances existentes...")
    
    all_balances = session.query(EECCDB).order_by(EECCDB.fecha_balance).all()
    
    for i, balance in enumerate(all_balances):
        print(f"Procesando balance {i+1}/{len(all_balances)} para empresa {balance.empresa.razon_social} ({balance.fecha_balance})...")
        
        # Obtener el balance anterior para el cálculo de scoring (si existe)
        prev_balance = session.query(EECCDB).filter(
            EECCDB.empresa_id == balance.empresa_id,
            EECCDB.fecha_balance < balance.fecha_balance
        ).order_by(EECCDB.fecha_balance.desc()).first()

        prev_result = prev_balance.resultado_neto if prev_balance else 0.0
        prev_ebitda = prev_balance.ebitda if prev_balance else 0.0
        
        prev_metrics = compute_metrics(prev_balance) if prev_balance else {}
        prev_equity = prev_metrics.get('patrimonio_neto') if prev_metrics else None

        # Calcular métricas y scoring para el balance actual
        current_metrics = compute_metrics(balance)
        scoring_results = calculate_scoring(current_metrics, prev_result, prev_equity, prev_ebitda)
        
        # Actualizar los campos del balance
        balance.score_solvencia = scoring_results['solvencia']
        balance.score_liquidez = scoring_results['liquidez']
        balance.score_rentabilidad = scoring_results['rentabilidad']
        balance.score_antiguedad = scoring_results['antiguedad']
        balance.score_nosis = scoring_results['nosis']
        balance.score_total = scoring_results['total']
        balance.score_label = scoring_results['label']
        balance.cupo_sugerido = scoring_results['cupo_sugerido']
        balance.multiplo_sugerido = scoring_results['multiplo']
        
        session.add(balance) # Marca el objeto para ser guardado

    try:
        session.commit() # Guarda todos los cambios en la base de datos
        print("Migración de datos completada exitosamente.")
    except Exception as e:
        session.rollback()
        print(f"Error durante la migración: {e}")
    finally:
        session.remove() # Cierra la sesión

if __name__ == "__main__":
    migrate_existing_balances()
