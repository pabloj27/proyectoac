import csv
import math
import io
import os
import sys
from datetime import date, datetime
from functools import wraps

from flask import Flask, redirect, render_template, request, url_for, make_response, jsonify, session as flask_session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv

from Base import EECCDB, EmpresaDB, session

# Cargar variables de entorno desde el archivo .env
# Esto permite encontrar el .env tanto en desarrollo como dentro del .exe de PyInstaller
if getattr(sys, 'frozen', False):
    # Si es un ejecutable, el .env está en la carpeta temporal _MEIPASS
    bundle_dir = sys._MEIPASS
    load_dotenv(os.path.join(bundle_dir, '.env'))
else:
    load_dotenv()

if getattr(sys, 'frozen', False):
    _template_folder = os.path.join(sys._MEIPASS, 'templates')
    app = Flask(__name__, template_folder=_template_folder)
else:
    app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "pzamora_roca_3312_key")

# Middleware para ver cada petición en la consola (Ayuda a saber si el clic llegó al servidor)
@app.before_request
def log_request_info():
    if not request.path.startswith('/static'):
        print(f"--> PETICIÓN: {request.method} {request.path}", flush=True)

# Decorador para proteger rutas
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not flask_session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == "pzamora" and request.form.get("password") == "Roca3312":
            flask_session["logged_in"] = True
            return redirect(request.args.get("next") or url_for("index"))
        return render_template("login.html", error="Usuario o contraseña incorrectos")
    return render_template("login.html")

@app.route("/logout")
def logout():
    flask_session.pop("logged_in", None)
    return redirect(url_for("login"))

@app.teardown_appcontext
def shutdown_session(exception=None):
    session.remove()

@app.template_filter('format_es')
def format_es(value, decimals=2):
    if value is None:
        return "0,00"
    try:
        format_str = "{:,." + str(decimals) + "f}"
        return format_str.format(float(value)).replace(',', 'X').replace('.', ',').replace('X', '.')
    except (ValueError, TypeError):
        return value

@app.template_filter('format_date')
def format_date(value):
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, '%Y-%m-%d').date()
        except:
            return value
    return value.strftime('%d/%m/%Y')

def parse_date(date_str):
    if not date_str:
        return None
    # Intenta formato estándar ISO (YYYY-MM-DD)
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        # Intenta formato manual DD/MM/YYYY
        try:
            return datetime.strptime(date_str, '%d/%m/%Y').date()
        except ValueError:
            return None


def safe_division(numerator: float, denominator: float) -> float:
    denominator = float(denominator)
    if abs(denominator) < 1e-9:
        return 0.0
    return numerator / denominator


def parse_float(value: str, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace('$', '')
    if not s:
        return default
    
    # Si tiene coma, tratamos los puntos como separadores de miles
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    # Si no tiene coma pero tiene un punto y parece ser formato mil (ej: 1.000)
    elif '.' in s and len(s.split('.')[-1]) == 3 and s.count('.') == 1:
        # Heurística: si es X.XXX sin comas, es probable que sea mil.
        s = s.replace('.', '')

    try:
        return float(s)
    except ValueError:
        return default


def compute_metrics(balance: EECCDB) -> dict:
    activo_total = balance.activo_corriente + balance.activo_no_corriente
    total_activo = max(activo_total, 1.0)
    ventas = max(balance.ventas, 1.0)
    pasivo_corriente = max(balance.pasivo_corriente, 1.0)
    deuda_financiera = balance.deuda_financiera
    ebitda = max(balance.ebitda, 1.0)
    capital_trabajo = balance.activo_corriente - balance.pasivo_corriente
    flujo_generado = balance.flujo_caja_operativo - balance.variacion_capital_trabajo
    flujo_libre = balance.flujo_caja_operativo - balance.capex - balance.dividendos
    
    total_pasivo = balance.pasivo_corriente + balance.pasivo_no_corriente
    patrimonio_neto = activo_total - total_pasivo

    solvencia = {
        "PN/Activos": round(safe_division(patrimonio_neto, total_activo) * 100, 2),
        "Deuda Financiera/Activos": round(safe_division(deuda_financiera, total_activo) * 100, 2),
        "Deuda Financiera/EBITDA": round(safe_division(deuda_financiera, ebitda), 2),
    }

    liquidez = {
        "Capital de Trabajo": round(capital_trabajo, 2),
        "Liquidez": round(safe_division(balance.activo_corriente, pasivo_corriente), 2),
        "Prueba Ácida": round(safe_division(balance.activo_corriente - balance.bienes_de_cambio, pasivo_corriente), 2),
        "Disponibilidades/Deuda Financiera": round(safe_division(balance.disponibilidades, deuda_financiera), 2),
        "Capital de Trabajo / Ventas": round(safe_division(capital_trabajo, ventas) * 100, 2),
    }

    flujo_caja = {
        "Flujo Generado por Operaciones (FGO)": round(flujo_generado, 2),
        "Cambios en el WC": round(balance.variacion_capital_trabajo, 2),
        "Flujo de Caja Operativo": round(balance.flujo_caja_operativo, 2),
        "FGO / Ventas": round(safe_division(flujo_generado, ventas) * 100, 2),
        "Flujo Operativo / Ventas": round(safe_division(balance.flujo_caja_operativo, ventas) * 100, 2),
        "Flujo Libre / Ventas": round(safe_division(flujo_libre, ventas) * 100, 2),
    }

    rentabilidad = {
        "ROE": round(safe_division(balance.resultado_neto, patrimonio_neto) * 100, 2),
        "EBITDA%": round(safe_division(balance.ebitda, ventas) * 100, 2),
    }

    return {
        "solvencia": solvencia,
        "liquidez": liquidez,
        "flujo_de_caja": flujo_caja,
        "rentabilidad": rentabilidad,
        "patrimonio_neto": patrimonio_neto,
        "ebitda_raw": balance.ebitda,
        "resultado_neto": balance.resultado_neto,
        "resultado_operativo": balance.resultado_operativo,
        "flujo_caja_operativo_raw": balance.flujo_caja_operativo,
        "flujo_generado_raw": flujo_generado,
        "variacion_wc_raw": balance.variacion_capital_trabajo,
        "ventas_raw": balance.ventas,
        "capital_trabajo_raw": capital_trabajo,
        "otros": {
            "Antigüedad (años)": balance.fecha_balance.year - (balance.empresa.anio_fundacion or balance.fecha_balance.year),
            "Score NOSIS": balance.nosis_score or 0
        }
    }

# Configuración de umbrales para el Scoring
SCORING_THRESHOLDS = {
    'solvencia_pn_activos': {
        'high': {'limit': 40.0, 'points': 5.0},
        'mid':  {'limit': 20.0, 'points': 2.5}
    }
}

def calculate_suggested_limit(equity: float, score: float) -> tuple:
    if equity <= 0: return 0.0, 0.0
    
    # Escalas discretas: se evalúa sobre el score redondeado a 1 decimal
    # para evitar inconsistencias visuales (ej: 6.96 que se ve como 7.0)
    s = round(score, 1)

    if s >= 9.0: factor = 1.5
    elif s >= 8.0: factor = 1.3
    elif s >= 7.0: factor = 1.2
    elif s >= 6.0: factor = 1.0
    elif s >= 5.0: factor = 0.7
    elif s >= 4.0: factor = 0.5
    else: factor = 0.0

    return equity * factor, factor

def calculate_scoring(metrics: dict, prev_result: float = 0.0, prev_equity: float = None, prev_ebitda: float = 0.0, prev_ebit: float = 0.0, prev_ventas: float = 0.0) -> dict:
    if not metrics: return {}
    
    # --- 1. LIQUIDEZ (Peso 30%) ---
    liq_val = metrics['liquidez']['Liquidez']
    acid_val = metrics['liquidez']['Prueba Ácida']
    ct_ventas = metrics['liquidez']['Capital de Trabajo / Ventas']
    
    s_liq = 0
    if liq_val >= 1.0:
        # Liquidez Corriente (Max 5.0 pts) - Umbrales relajados
        s_liq += 5.0 if liq_val > 1.4 else (3.5 if liq_val > 1.15 else 2.0)
        
        # Prueba Ácida (Max 3.0 pts) - Inclusión de nuevo método
        s_liq += 3.0 if acid_val > 1.0 else (2.0 if acid_val > 0.8 else (1.0 if acid_val > 0.5 else 0))
        
        # Capital de Trabajo / Ventas (Max 2.0 pts) - Umbrales relajados (8% vs 10%)
        s_liq += 2.0 if ct_ventas > 8.0 else (1.0 if ct_ventas > 4.0 else 0)

    s_liq = min(10.0, s_liq)
    
    # --- 2. SOLVENCIA (Peso 25%) ---
    pn_activos = metrics['solvencia']['PN/Activos']
    deuda_ebitda = metrics['solvencia']['Deuda Financiera/EBITDA']
    
    s_sol = 0
    conf_pn = SCORING_THRESHOLDS['solvencia_pn_activos']
    if pn_activos > conf_pn['high']['limit']:
        s_sol += 5.0
    elif pn_activos > conf_pn['mid']['limit']:
        s_sol += 2.5

    if deuda_ebitda > 0:
        s_sol += 5.0 if deuda_ebitda < 2.5 else (2.5 if deuda_ebitda < 4.5 else 0)
    else: s_sol += 5.0

    # --- 3. RENTABILIDAD Y FLUJO (Peso 20%) ---
    roe = metrics['rentabilidad']['ROE']
    ebitda_pct = metrics['rentabilidad']['EBITDA%']
    fcf_ventas = metrics['flujo_de_caja']['Flujo Libre / Ventas']
    
    # Nueva lógica solicitada: (Res. Anterior + Res. Actual) / PN Actual
    res_act = metrics.get('resultado_neto', 0.0)
    pn_act = metrics.get('patrimonio_neto', 1.0)
    roe_compuesto = safe_division(res_act + prev_result, pn_act) * 100
    
    s_rent = 0
    s_rent += 4.0 if ebitda_pct > 15.0 else (2.0 if ebitda_pct > 7.0 else 0)
    
    # Ajuste de umbral a 60% según solicitud para capturar casos de recuperación fuerte
    if roe_compuesto > 60.0 or roe > 15.0:
        s_rent += 3.0
    elif roe > 5.0 or roe_compuesto > 20.0:
        s_rent += 1.5

    s_rent += 3.0 if fcf_ventas > 5.0 else (1.5 if fcf_ventas > 0 else 0)

    # Garantía: resultado neto positivo siempre produce score > 5
    if res_act > 0:
        s_rent = max(s_rent, 5.5)

    # --- 4. ANTIGÜEDAD (Peso 10%) ---
    ant = metrics['otros']['Antigüedad (años)']
    s_age = 10.0 if ant >= 30 else (7.0 if ant >= 10 else (4.0 if ant >= 5 else 0))

    # --- 5. NOSIS (Peso 15%) ---
    nos = metrics['otros']['Score NOSIS']
    # Detectar si el score viene en escala 0-1000 y normalizar a 0-10
    # Si el valor es > 10.0, se asume escala 1-999 y se divide por 100
    val_nos = nos / 100.0 if nos > 10.1 else nos
    s_nos = min(10.0, max(0.0, val_nos))

    # --- AJUSTE DE TENDENCIA YoY (±1.5 pts) ---
    ajuste_tendencia = 0.0
    has_prev_data = bool(prev_ventas or prev_ebitda or (prev_equity is not None and prev_equity != 0))

    if has_prev_data:
        ventas_act = metrics.get('ventas_raw', 0.0)
        ebitda_act = metrics.get('ebitda_raw', 0.0)
        pn_act_val = metrics.get('patrimonio_neto', 0.0)

        if prev_ventas and abs(prev_ventas) > 1e-9:
            pct_v = (ventas_act - prev_ventas) / abs(prev_ventas) * 100
            if   pct_v >= 15:  adj_v =  0.50
            elif pct_v >=  5:  adj_v =  0.25
            elif pct_v >= -5:  adj_v =  0.0
            elif pct_v >= -15: adj_v = -0.25
            else:              adj_v = -0.50
        else:
            adj_v = 0.0

        if prev_ebitda and abs(prev_ebitda) > 1e-9:
            pct_e = (ebitda_act - prev_ebitda) / abs(prev_ebitda) * 100
            if   pct_e >= 20:  adj_e =  0.50
            elif pct_e >=  5:  adj_e =  0.25
            elif pct_e >= -5:  adj_e =  0.0
            elif pct_e >= -15: adj_e = -0.25
            else:              adj_e = -0.50
        else:
            adj_e = 0.0

        if prev_equity is not None and abs(prev_equity) > 1e-9:
            pct_pn = (pn_act_val - prev_equity) / abs(prev_equity) * 100
            if   pct_pn >= 10: adj_pn =  0.50
            elif pct_pn >=  0: adj_pn =  0.25
            elif pct_pn >= -5: adj_pn = -0.25
            else:              adj_pn = -0.50
        else:
            adj_pn = 0.0

        ajuste_tendencia = max(-1.5, min(1.5, adj_v + adj_e + adj_pn))
        print(f"DEBUG TENDENCIA: Ventas={adj_v:+.2f}, EBITDA={adj_e:+.2f}, PN={adj_pn:+.2f} → Ajuste={ajuste_tendencia:+.2f}", flush=True)

    # --- CÁLCULO FINAL (Escala 0-10) ---
    print(f"DEBUG SCORING [{metrics['otros'].get('Score NOSIS')}]: Liq={s_liq:.2f}, Sol={s_sol:.2f}, Rent={s_rent:.2f}, Age={s_age:.2f}, Nos={s_nos:.2f}, Tend={ajuste_tendencia:+.2f}", flush=True)
    base_score = (s_liq * 0.30) + (s_sol * 0.25) + (s_rent * 0.20) + (s_age * 0.10) + (s_nos * 0.15)
    final_score = max(0.0, min(10.0, base_score + ajuste_tendencia))

    # --- EVALUACIÓN DE HARD STOPS (No afectan el puntaje numérico) ---
    has_hard_stop = False
    hard_stop_reasons = []
    
    pn_actual = metrics.get('patrimonio_neto', 0.0)
    activo_total = pn_actual + (metrics.get('solvencia', {}).get('PN/Activos', 0) / 100 if pn_actual != 0 else 1) # Simplificado para el ejemplo
    # En la realidad usamos el valor real de activos que calculamos en compute_metrics
    # 1. Patrimonio neto negativo o técnicamente quebrado (PN < 0 o PN/Activo < 5%)
    pn_sobre_activo = metrics['solvencia']['PN/Activos']
    if pn_actual < 0 or pn_sobre_activo < 5.0:
        has_hard_stop = True
        hard_stop_reasons.append("Patrimonio Neto negativo o Solvencia crítica (PN/Activo < 5%)")

    # 2. Incapacidad de pago inmediato (Liquidez Ácida < 0.30)
    prueba_acida = metrics['liquidez']['Prueba Ácida']
    if prueba_acida < 0.30:
        has_hard_stop = True
        hard_stop_reasons.append("Liquidez Ácida crítica (< 0.30)")

    # 4. EBITDA negativo sostenido (EBITDA < 0 en últimos 2 años)
    ebitda_actual = metrics.get('ebitda_raw', 0.0)
    if ebitda_actual < 0 and prev_ebitda < 0:
        has_hard_stop = True
        hard_stop_reasons.append("EBITDA negativo recurrente (Pérdida operativa sostenida)")

    # 5. Score NOSIS menor a 2.0 (Se mantiene como criterio de integridad en escala 0-10)
    if 0 < s_nos < 2.0:
        has_hard_stop = True
        hard_stop_reasons.append("Score NOSIS por debajo del umbral mínimo de integridad (< 2.0)")

    # 4. Liquidez Operativa Crítica (CT + FGO < 0)
    ct = metrics['liquidez']['Capital de Trabajo']
    fgo = metrics.get('flujo_de_caja', {}).get('Flujo Generado por Operaciones (FGO)', 0.0)
    if (ct + fgo) < 0:
        has_hard_stop = True
        hard_stop_reasons.append("Flujo Generado + Capital de Trabajo negativo (Liquidez operativa crítica)")

    display_score = int(round(final_score, 0))
    label = "D"
    color = "#dc3545" # Rojo
    
    if not has_hard_stop:
        if final_score >= 8.5: 
            label = "A (Excelente)"
            color = "#198754"
        elif final_score >= 7.0: 
            label = "B (Muy Bueno)"
            color = "#20c997"
        elif final_score >= 5.0: 
            label = "C (Aceptable)"
            color = "#ffc107"
    else:
        label = "D (Rechazo por Hard Stop)"

    # Cálculo de Cupo Sugerido basado en escalas y PN Actual
    pn_actual = metrics.get('patrimonio_neto', 0.0)
    ventas_raw = metrics.get('ventas_raw', 0.0)
    ct_raw = metrics.get('capital_trabajo_raw', 0.0)
    ebitda_raw = metrics.get('ebitda_raw', 0.0)

    if has_hard_stop:
        cupo_sugerido, multiplo = 0.0, 0.0
    else:
        _, multiplo = calculate_suggested_limit(pn_actual, display_score)
        cupo_patrimonio = max(0, pn_actual * multiplo)
        cupo_ventas = max(0, ventas_raw * 0.15 * multiplo)
        cupo_ct = max(0, ct_raw * 0.80 * multiplo)
        cupo_ebitda = max(0, ebitda_raw * 3.0 * multiplo)
        
        # El cupo por operaciones es el promedio del Cupo por Ventas, por CT y por EBITDA
        cupo_negocio_avg = (cupo_ventas + cupo_ct + cupo_ebitda) / 3
        cupo_sugerido = min(cupo_patrimonio, cupo_negocio_avg if cupo_negocio_avg > 0 else cupo_patrimonio)
    
    # Logs de depuración en consola para monitorear el cálculo
    print(f"--- DETALLE DE CALCULO CUPO ---")
    print(f"Patrimonio: {pn_actual:,.2f}")
    print(f"Score Real: {final_score} | Score Redondeado: {display_score}")
    print(f"Múltiplo Aplicado: {multiplo}")
    print(f"Cupo Sugerido Resultante: {cupo_sugerido:,.2f}")
    print(f"-------------------------------")
    
    return {
        "liquidez": round(s_liq, 1),
        "solvencia": round(s_sol, 1),
        "rentabilidad": round(s_rent, 1),
        "antiguedad": round(s_age, 1),
        "nosis": round(s_nos, 1),
        "ajuste_tendencia": round(ajuste_tendencia, 2),
        "total": float(display_score),
        "cupo_sugerido": cupo_sugerido,
        "pn_actual": pn_actual,
        "multiplo": multiplo,
        "label": label,
        "color": color,
        "hard_stops": hard_stop_reasons
    }

def variation_label(actual: float, comparativo: float) -> str:
    if actual > comparativo:
        return "Mejora"
    if actual < comparativo:
        return "Empeora"
    return "Sin cambio"


@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/help")
def help():
    try:
        return render_template("help.html")
    except Exception:
        return "Archivo help.html no encontrado en la carpeta templates.", 404

@app.route("/tech-docs")
def tech_docs():
    try:
        return render_template("technical_docs.html")
    except Exception:
        return "Archivo technical_docs.html no encontrado.", 404

@app.route("/generate_description", methods=["POST"])
def generate_description():
    data = request.get_json(silent=True) or {}
    razon_social = data.get("razon_social")
    cuit        = data.get("cuit") or "No informado"
    sector      = data.get("sector") or "No informado"

    if not razon_social:
        return jsonify({"error": "Debe proporcionar la Razón Social de la empresa para buscar información."}), 400

    try:
        from google import genai as google_genai
        from google.genai import types as genai_types

        api_key = os.environ.get("GOOGLE_API_KEY")
        client = google_genai.Client(api_key=api_key)

        prompt_text = f"""Buscá en Google información real y verificable sobre la siguiente empresa argentina y redactá un perfil breve.

EMPRESA:
- Razón Social: {razon_social}
- CUIT: {cuit}
- Sector: {sector}

CONTENIDO A INCLUIR (máximo 120 palabras):
- Actividad principal y líneas de negocio
- Antigüedad / año de fundación (si se encuentra)
- Ubicación operativa
- Grupo empresarial al que pertenece (si aplica)
- Noticias relevantes: proyectos, obras, contratos (con fuente)
- Alertas negativas si las hay: concursos preventivos, quiebras, inhibiciones, deudas fiscales, problemas judiciales (con fuente)

REGLAS ESTRICTAS:
- Solo incluir información encontrada mediante búsqueda web en este momento. No usar memoria de entrenamiento.
- Si no encontrás información verificable sobre algún punto, simplemente omitilo. No inventar ni suponer datos.
- Responder directamente con el contenido, sin frases introductorias.
- Lenguaje directo y profesional."""

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt_text,
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                temperature=0.1,
            ),
        )

        text = response.text
        sources = []

        try:
            candidate = response.candidates[0]
            gm = getattr(candidate, 'grounding_metadata', None)
            if gm:
                seen = set()
                for chunk in getattr(gm, 'grounding_chunks', []):
                    web = getattr(chunk, 'web', None)
                    if web:
                        uri   = getattr(web, 'uri', '') or ''
                        title = getattr(web, 'title', '') or uri
                        if uri and uri not in seen:
                            sources.append({"title": title, "url": uri})
                            seen.add(uri)
        except Exception as meta_err:
            print(f"WARNING generate_description: no se pudo extraer fuentes: {meta_err}", flush=True)

        if text:
            grounding_used = len(sources) > 0
            print(f"SUCCESS generate_description: {len(text)} chars, {len(sources)} fuentes. Grounding activo: {grounding_used}", flush=True)
            if not grounding_used:
                return jsonify({"error": "No se encontró información verificable en la web sobre esta empresa. No se generó descripción para evitar datos incorrectos."}), 404
            return jsonify({"description": text, "sources": sources})
        return jsonify({"error": "La IA no devolvió contenido."}), 500

    except Exception as e:
        print(f"ERROR generate_description: {str(e)}", flush=True)
        return jsonify({"error": f"Error al generar perfil: {str(e)}"}), 500


@app.route("/generate_risk_analysis", methods=["POST"])
@login_required
def generate_risk_analysis():
    print("\n--> RECIBIDA PETICIÓN PARA ANÁLISIS DE RIESGO IA", flush=True)
    data = request.get_json(silent=True) or {}
    
    # Extraemos los datos calculados enviados desde el cliente
    metrics = data.get("metrics") or {}
    scoring = data.get("scoring") or {}
    company = data.get("company") or {}
    balance = data.get("balance") or {}
    print(f"DEBUG IA PAYLOAD: Metrics={metrics.get('flujo_de_caja')}", flush=True)
    
    razon_social = company.get('razon_social') or "Empresa Desconocida"
    print(f"DEBUG: Generando análisis para {razon_social}...", flush=True)

    try:
        from google import genai as google_genai
        from google.genai import types as genai_types

        api_key = os.environ.get("GOOGLE_API_KEY")
        client = google_genai.Client(api_key=api_key)

        hard_stops_text = ", ".join(scoring.get("hard_stops", [])) if scoring.get("hard_stops") else "Ninguno"

        ventas         = balance.get('ventas') or 0
        ebitda         = balance.get('ebitda') or 0
        resultado_neto = balance.get('resultado_neto') or 0
        patrimonio_neto = balance.get('patrimonio_neto') or 0
        solvencia      = metrics.get('solvencia', {}).get('PN/Activos') or 0
        liquidez       = metrics.get('liquidez', {}).get('Liquidez') or 0
        apalancamiento = metrics.get('solvencia', {}).get('Deuda Financiera/EBITDA') or 0
        roe            = metrics.get('rentabilidad', {}).get('ROE') or 0
        fgo            = metrics.get('flujo_de_caja', {}).get('Flujo Generado por Operaciones (FGO)', 0)
        variacion_wc   = metrics.get('flujo_de_caja', {}).get('Cambios en el WC', 0)
        fco            = metrics.get('flujo_de_caja', {}).get('Flujo de Caja Operativo', 0)
        score          = scoring.get('total') or 0
        label          = scoring.get('label') or "D"
        sector         = company.get('sector') or "N/A"
        fecha_actual   = company.get('fecha_actual') or "período actual"
        fecha_anterior = company.get('fecha_anterior') or "período anterior"

        # Datos del período anterior para análisis de evolución
        balance_anterior  = data.get("balance_anterior") or {}
        metrics_anterior  = data.get("metrics_anterior") or {}
        prev_ventas       = balance_anterior.get('ventas') or 0
        prev_ebitda       = balance_anterior.get('ebitda') or 0
        prev_resultado_neto = balance_anterior.get('resultado_neto') or 0
        prev_patrimonio_neto = balance_anterior.get('patrimonio_neto') or 0
        prev_liquidez     = (metrics_anterior.get('liquidez') or {}).get('Liquidez') or 0

        has_anterior = bool(prev_ventas or prev_ebitda or prev_patrimonio_neto)

        def _var_pct(actual, anterior):
            if not anterior or abs(anterior) < 1e-9:
                return None
            return ((actual - anterior) / abs(anterior)) * 100

        if has_anterior:
            vv = _var_pct(ventas, prev_ventas)
            ve = _var_pct(ebitda, prev_ebitda)
            vp = _var_pct(patrimonio_neto, prev_patrimonio_neto)
            evol_lineas = []
            if vv is not None:
                evol_lineas.append(f"- Ventas: {'+' if vv >= 0 else ''}{vv:.1f}% (${prev_ventas:,.0f} → ${ventas:,.0f})")
            if ve is not None:
                evol_lineas.append(f"- EBITDA: {'+' if ve >= 0 else ''}{ve:.1f}% (${prev_ebitda:,.0f} → ${ebitda:,.0f})")
            if vp is not None:
                evol_lineas.append(f"- Patrimonio Neto: {'+' if vp >= 0 else ''}{vp:.1f}% (${prev_patrimonio_neto:,.0f} → ${patrimonio_neto:,.0f})")
            if prev_liquidez:
                evol_lineas.append(f"- Liquidez Corriente: {prev_liquidez:.2f}x → {liquidez:.2f}x")
            evolucion_bloque = f"\nEvolución Interanual ({fecha_anterior} → {fecha_actual}):\n" + "\n".join(evol_lineas)
            req_evolucion = "\n4. **Evolución Interanual:** Comenta sobre la variación en Ventas, EBITDA y Patrimonio Neto respecto al período anterior. Indica si la tendencia es positiva, negativa o estable y qué la explica."
            req_conclusion_num = "5"
        else:
            evolucion_bloque = ""
            req_evolucion = ""
            req_conclusion_num = "4"

        prompt_text = f"""Eres un Credit Risk Analyst de una agencia calificadora internacional (estándar Moody's/S&P). Redacta análisis de riesgo crediticio basado en datos financieros verificados.

ENTIDAD Y CONTEXTO:
- Empresa: {razon_social}
- Sector: {sector}
- Período: {fecha_actual}

DATA FINANCIERA (en ARS):

Resultados:
- Ventas Netas: ${ventas:,.0f}
- EBITDA: ${ebitda:,.0f}
- Resultado Neto: ${resultado_neto:,.0f}

Solvencia & Liquidez:
- Patrimonio Neto / Activos: {solvencia}%
- Liquidez Corriente: {liquidez}x
- Apalancamiento (Deuda Financiera / EBITDA): {apalancamiento}x

Rentabilidad:
- ROE (Retorno Patrimonio): {roe}%

Flujos de Caja:
- Flujo Generado por Operaciones: ${fgo:,.0f}
- Variación de Capital de Trabajo: ${variacion_wc:,.0f}
- Flujo de Caja Operativo (FCO): ${fco:,.0f}
{evolucion_bloque}
RATING Y ALERTAS:
- Score Modelo: {score}/10
- Calificación: {label}
- Disparadores Críticos (Hard Stops): {hard_stops_text}

REQUERIMIENTOS:
1. **Síntesis Patrimonial:** Describe la estructura de balance, solvencia y calidad de activos.
2. **Análisis de Resultados:** Evalúa márgenes operativos, rentabilidad, tendencias.
3. **Capacidad de Pago:** Analiza generación de flujos para repago de deudas financieras y comerciales.{req_evolucion}
{req_conclusion_num}. **Conclusión:** Justifica la calificación asignada. Si existen Hard Stops, mencionalos.

ESTILO:
- Lenguaje técnico-financiero profesional
- Máximo 250 palabras
- Estructura en párrafos claros (no listas)
- Sin saludos ni introducciones
- Directo y fundamentado en datos
- Evitar términos exagerados o hiperbólicos como "excepcionalmente", "sobresaliente", "extraordinario", "notable", "excelente", "impresionante" u otros superlativos no respaldados por los datos. Usar descripciones precisas y moderadas.
- CRÍTICO: No incluyas ninguna afirmación sobre situación externa de la empresa (litigios, concursos, defaults, noticias) que no provenga de una búsqueda web verificable en este momento. Toda información externa debe tener fuente."""

        print(f"DEBUG: Payload para LLM preparado. Hard Stops: {len(scoring.get('hard_stops', []))}", flush=True)

        gen_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt_text,
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                temperature=0.2,
            ),
        )

        text = gen_response.text
        sources = []

        try:
            candidate = gen_response.candidates[0]
            gm = getattr(candidate, 'grounding_metadata', None)
            if gm:
                seen = set()
                for chunk in getattr(gm, 'grounding_chunks', []):
                    web = getattr(chunk, 'web', None)
                    if web:
                        uri   = getattr(web, 'uri', '') or ''
                        title = getattr(web, 'title', '') or uri
                        if uri and uri not in seen:
                            sources.append({"title": title, "url": uri})
                            seen.add(uri)
        except Exception as meta_err:
            print(f"WARNING: No se pudo extraer fuentes del grounding: {meta_err}", flush=True)

        if text:
            print(f"SUCCESS: Análisis generado ({len(text)} chars). Fuentes: {len(sources)}.", flush=True)
            return jsonify({"analysis": text, "sources": sources})
        else:
            print("WARNING: La IA devolvió una respuesta vacía.", flush=True)
            return jsonify({"error": "La IA no devolvió contenido."}), 500

    except Exception as e:
        print(f"ERROR CRÍTICO EN IA: {str(e)}", flush=True)
        return jsonify({"error": f"Error en generación de análisis: {str(e)}"}), 500


@app.route("/entry", methods=["GET", "POST"])
@login_required
def entry():
    try:
        companies = session.query(EmpresaDB).order_by(EmpresaDB.razon_social).all()
    except Exception:
        session.rollback()
        companies = session.query(EmpresaDB).order_by(EmpresaDB.razon_social).all()

    today_year = date.today().year
    selected_company = None
    actual_balance = None
    comparativo_balance = None
    results = None
    message = None
    previous_balances = []
    available_dates = []
    actual_scoring = {}
    comparativo_scoring = {}
    actual_metrics = {}
    comparativo_metrics = {}
    sel_date = None
    comp_date = None

    # Obtenemos las fechas de los argumentos de la URL (para recarga por búsqueda)
    selected_date_str = request.args.get("selected_date")
    comparativo_date_str = request.args.get("comparativo_date")
    raw_selected_date = selected_date_str
    raw_comparativo_date = comparativo_date_str
    
    # Formatear fechas para el buscador si vienen de la DB (ISO a Local)

    if request.method == "GET":
        company_id = request.args.get("company_id", type=int)
        if company_id:
            selected_company = session.get(EmpresaDB, company_id)
            if selected_company:
                previous_balances = (
                    session.query(EECCDB)
                    .filter_by(empresa_id=company_id)
                    .order_by(EECCDB.fecha_balance.desc())
                    .all()
                )
                available_dates = [b.fecha_balance for b in previous_balances]
                
                # Si no se pasaron fechas por URL, intentamos pre-cargar las últimas disponibles
                if selected_date_str is None and available_dates and request.args.get("new") != "1":
                    selected_date_str = available_dates[0].strftime('%d/%m/%Y')
                if comparativo_date_str is None and len(available_dates) > 1:
                    comparativo_date_str = available_dates[1].strftime('%d/%m/%Y')
                elif comparativo_date_str is None and selected_date_str:
                    # Fallback por si solo hay una fecha o el usuario escribió una
                    pass

                sel_date = parse_date(selected_date_str)
                comp_date = parse_date(comparativo_date_str)

                # Intentar cargar balance actual
                actual_balance = (
                    session.query(EECCDB)
                    .filter_by(empresa_id=company_id, fecha_balance=sel_date)
                    .first()
                )
                # Intentar cargar balance comparativo
                comparativo_balance = (
                    session.query(EECCDB)
                    .filter_by(empresa_id=company_id, fecha_balance=comp_date)
                    .first()
                )
    
    elif request.method == "POST":
        # Capturar fechas crudas para devolverlas al template en caso de error
        raw_selected_date = request.form.get("selected_date")
        raw_comparativo_date = request.form.get("comparativo_date")
        
        # Validar fechas antes de procesar
        sel_date = parse_date(raw_selected_date)
        comp_date = parse_date(raw_comparativo_date)
        
        if raw_selected_date and not sel_date and not message:
            message = "Error: El formato de la Fecha Actual es inválido."
        if raw_comparativo_date and not comp_date and not message:
            message = "Error: El formato de la Fecha Comparativa es inválido."

        # Si hay error de fecha, devolvemos temprano para no perder datos
        if message and "Error" in message:
            return render_template(
                "entry.html", companies=companies, selected_company=selected_company,
                selected_date=raw_selected_date, comparativo_date=raw_comparativo_date,
                actual_metrics={}, comparativo_metrics={}, message=message, previous_balances=[]
            )

        try:
            company_id = request.form.get("company_id")
            if company_id and company_id.isdigit():
                selected_company = session.get(EmpresaDB, int(company_id))

            cuit = request.form.get("cuit", "").strip()
            razon_social = request.form.get("razon_social", "").strip()
            
            # Conversión segura de año de fundación
            raw_anio = request.form.get("anio_fundacion", "").strip()
            anio_fundacion = int(raw_anio) if raw_anio.isdigit() else None

            if cuit and (not cuit.isdigit() or len(cuit) != 11):
                raise ValueError("El CUIT debe tener exactamente 11 dígitos numéricos.")
            sector = request.form.get("sector", "").strip()

            if selected_company is None:
                selected_company = session.query(EmpresaDB).filter_by(cuit=cuit).first()
                
                if selected_company is None:
                    if not cuit or not razon_social:
                        raise ValueError("Debe ingresar CUIT y Razón Social.")
                    selected_company = EmpresaDB(
                        cuit=cuit,
                        razon_social=razon_social,
                        actividad=request.form.get("actividad", ""),
                        sector=sector,
                        anio_fundacion=anio_fundacion,
                        descripcion=request.form.get("descripcion", ""),
                    )
                    session.add(selected_company)
                    session.flush()
                    message = "Empresa nueva guardada."
            
            if selected_company:
                # Verificar si estamos intentando cambiar el CUIT a uno que ya tiene OTRA empresa
                if selected_company.cuit != cuit:
                    conflict = session.query(EmpresaDB).filter(EmpresaDB.cuit == cuit, EmpresaDB.id != selected_company.id).first()
                    if conflict:
                        raise ValueError(f"El CUIT {cuit} ya pertenece a la empresa '{conflict.razon_social}'.")
                
                selected_company.cuit = cuit
                selected_company.razon_social = razon_social
                selected_company.actividad = request.form.get("actividad", "")
                selected_company.sector = sector
                selected_company.anio_fundacion = anio_fundacion
                selected_company.descripcion = request.form.get("descripcion", "")
                if message is None:
                    message = "Empresa actualizada."

            if sel_date:
                actual_balance = (
                    session.query(EECCDB)
                    .filter_by(empresa_id=selected_company.id, fecha_balance=sel_date)
                    .first()
                )
                if actual_balance is None:
                    actual_balance = EECCDB(fecha_balance=sel_date, empresa=selected_company)
                actual_balance.anio = sel_date.year
                actual_balance.disponibilidades = parse_float(request.form.get("actual_disponibilidades", "0.0"))
                actual_balance.bienes_de_cambio = parse_float(request.form.get("actual_bienes_de_cambio", "0.0"))
                actual_balance.activo_corriente = parse_float(request.form.get("actual_activo_corriente", "0.0"))
                actual_balance.activo_no_corriente = parse_float(request.form.get("actual_activo_no_corriente", "0.0"))
                actual_balance.pasivo_corriente = parse_float(request.form.get("actual_pasivo_corriente", "0.0"))
                actual_balance.pasivo_no_corriente = parse_float(request.form.get("actual_pasivo_no_corriente", "0.0"))
                actual_balance.deuda_financiera = parse_float(request.form.get("actual_deuda_financiera", "0.0"))
                actual_balance.ventas = parse_float(request.form.get("actual_ventas", "0.0"))
                actual_balance.resultado_operativo = parse_float(request.form.get("actual_resultado_operativo", "0.0"))
                actual_balance.ebitda = parse_float(request.form.get("actual_ebitda", "0.0"))
                actual_balance.resultado_neto = parse_float(request.form.get("actual_resultado_neto", "0.0"))
                actual_balance.flujo_caja_operativo = parse_float(request.form.get("actual_flujo_caja_operativo", "0.0"))
                actual_balance.variacion_capital_trabajo = parse_float(request.form.get("actual_variacion_capital_trabajo", "0.0"))
                actual_nosis = parse_float(request.form.get("actual_nosis_score", "0"))
                if actual_nosis > 1000.0:
                    raise ValueError("El Score NOSIS actual no puede superar los 1000 puntos.")
                actual_balance.nosis_score = actual_nosis
                actual_balance.capex = parse_float(request.form.get("actual_capex", "0.0"))
                actual_balance.dividendos = parse_float(request.form.get("actual_dividendos", "0.0"))
                actual_balance.analisis = request.form.get("actual_analisis", "")
                session.add(actual_balance)

            if comp_date:
                comparativo_balance = (
                    session.query(EECCDB)
                    .filter_by(empresa_id=selected_company.id, fecha_balance=comp_date)
                    .first()
                )
                if comparativo_balance is None:
                    comparativo_balance = EECCDB(fecha_balance=comp_date, empresa=selected_company)
                comparativo_balance.anio = comp_date.year
                comparativo_balance.disponibilidades = parse_float(request.form.get("comparativo_disponibilidades", "0.0"))
                comparativo_balance.bienes_de_cambio = parse_float(request.form.get("comparativo_bienes_de_cambio", "0.0"))
                comparativo_balance.activo_corriente = parse_float(request.form.get("comparativo_activo_corriente", "0.0"))
                comparativo_balance.activo_no_corriente = parse_float(request.form.get("comparativo_activo_no_corriente", "0.0"))
                comparativo_balance.pasivo_corriente = parse_float(request.form.get("comparativo_pasivo_corriente", "0.0"))
                comparativo_balance.pasivo_no_corriente = parse_float(request.form.get("comparativo_pasivo_no_corriente", "0.0"))
                comparativo_balance.deuda_financiera = parse_float(request.form.get("comparativo_deuda_financiera", "0.0"))
                comparativo_balance.ventas = parse_float(request.form.get("comparativo_ventas", "0.0"))
                comparativo_balance.resultado_operativo = parse_float(request.form.get("comparativo_resultado_operativo", "0.0"))
                comparativo_balance.ebitda = parse_float(request.form.get("comparativo_ebitda", "0.0"))
                comparativo_balance.resultado_neto = parse_float(request.form.get("comparativo_resultado_neto", "0.0"))
                comparativo_balance.flujo_caja_operativo = parse_float(request.form.get("comparativo_flujo_caja_operativo", "0.0"))
                comparativo_balance.variacion_capital_trabajo = parse_float(request.form.get("comparativo_variacion_capital_trabajo", "0.0"))
                print(f"DEBUG ENTRY: Comparativo FCO={comparativo_balance.flujo_caja_operativo}, VarWC={comparativo_balance.variacion_capital_trabajo}", flush=True)
                comp_nosis = parse_float(request.form.get("comparativo_nosis_score", "0"))
                if comp_nosis > 1000.0:
                    raise ValueError("El Score NOSIS comparativo no puede superar los 1000 puntos.")
                comparativo_balance.nosis_score = comp_nosis
                comparativo_balance.capex = parse_float(request.form.get("comparativo_capex", "0.0"))
                comparativo_balance.dividendos = parse_float(request.form.get("comparativo_dividendos", "0.0"))

                m_comp = compute_metrics(comparativo_balance)
                sc_comp = calculate_scoring(m_comp)
                comparativo_balance.score_solvencia = sc_comp['solvencia']
                comparativo_balance.score_liquidez = sc_comp['liquidez']
                comparativo_balance.score_rentabilidad = sc_comp['rentabilidad']
                comparativo_balance.score_antiguedad = sc_comp['antiguedad']
                comparativo_balance.score_nosis = sc_comp['nosis']
                comparativo_balance.score_total = sc_comp['total']
                comparativo_balance.score_label = sc_comp['label']
                comparativo_balance.cupo_sugerido = sc_comp['cupo_sugerido']
                comparativo_balance.multiplo_sugerido = sc_comp['multiplo']
                session.add(comparativo_balance)

            # Re-calcular y persistir el scoring del balance actual AHORA que el comparativo está actualizado
            if actual_balance:
                m_act = compute_metrics(actual_balance)
                res_ant = comparativo_balance.resultado_neto if comparativo_balance else 0.0
                eb_ant = comparativo_balance.ebitda if comparativo_balance else 0.0
                ebit_ant = comparativo_balance.resultado_operativo if comparativo_balance else 0.0
                m_comp_temp = compute_metrics(comparativo_balance) if comparativo_balance else {}
                pn_ant = m_comp_temp.get('patrimonio_neto')
                ven_ant = comparativo_balance.ventas if comparativo_balance else 0.0

                sc_act = calculate_scoring(m_act, res_ant, pn_ant, eb_ant, ebit_ant, ven_ant)
                actual_balance.score_solvencia = sc_act['solvencia']
                actual_balance.score_liquidez = sc_act['liquidez']
                actual_balance.score_rentabilidad = sc_act['rentabilidad']
                actual_balance.score_antiguedad = sc_act['antiguedad']
                actual_balance.score_nosis = sc_act['nosis']
                actual_balance.score_total = sc_act['total']
                actual_balance.score_label = sc_act['label']
                actual_balance.cupo_sugerido = sc_act['cupo_sugerido']
                actual_balance.multiplo_sugerido = sc_act['multiplo']

            session.commit()
            # Después del commit, refrescar los objetos de balance para asegurar que reflejan el último estado de la DB
            if actual_balance:
                session.expire(actual_balance) # Forzamos que se recargue de la DB en el próximo acceso
            if comparativo_balance:
                session.expire(comparativo_balance)
        except Exception as e:
            session.rollback()
            if isinstance(e, IntegrityError):
                # Intentar extraer un mensaje más útil de la excepción de SQLAlchemy
                error_info = str(e.orig) if hasattr(e, 'orig') else str(e)
                if "UNIQUE constraint failed: empresas.cuit" in error_info:
                    message = "Error: El CUIT ya está registrado para otra empresa."
                elif "UNIQUE constraint failed: estados_contables" in error_info:
                    message = "Error: Ya existe un balance cargado para esta empresa en la fecha seleccionada."
                else:
                    message = f"Error de integridad en base de datos: {error_info}"
            else:
                message = f"Error: {str(e)}"
            
            return render_template(
                "entry.html",
                companies=companies,
                selected_company=selected_company,
                actual_balance=actual_balance,
                comparativo_balance=comparativo_balance,
                selected_date=raw_selected_date,
                comparativo_date=raw_comparativo_date,
                available_dates=[],
                actual_metrics={},
                comparativo_metrics={},
                message=message,
                previous_balances=[],
            )

    previous_balances = []
    if selected_company:
        previous_balances = (
            session.query(EECCDB)
            .filter_by(empresa_id=selected_company.id)
            .order_by(EECCDB.fecha_balance.desc())
            .all()
        )
    
    # Recalcular métricas DESPUÉS de que los objetos de balance estén completamente actualizados/refrescados de la DB
    actual_metrics = compute_metrics(actual_balance) if actual_balance else {}
    comparativo_metrics = compute_metrics(comparativo_balance) if comparativo_balance else {}

    # SOLUCIÓN: Recalcular SIEMPRE antes de renderizar (Stateless View).
    # Esto ignora los valores persistidos stale y usa la lógica actual del código.
    if actual_balance:
        # Priorizar el balance comparativo presente en pantalla para el cálculo del score actual
        if comparativo_balance and (actual_balance is not comparativo_balance):
            p_res = comparativo_balance.resultado_neto
            p_eb = comparativo_balance.ebitda
            p_ebit = comparativo_balance.resultado_operativo
            p_eq = comparativo_metrics.get('patrimonio_neto')
            p_ven = comparativo_balance.ventas
        else:
            # Si no hay comparativo en pantalla, buscar en DB
            prev_b = session.query(EECCDB).filter(
                EECCDB.empresa_id == selected_company.id,
                EECCDB.fecha_balance < actual_balance.fecha_balance
            ).order_by(EECCDB.fecha_balance.desc()).first()

            p_res = prev_b.resultado_neto if prev_b else 0.0
            p_eb = prev_b.ebitda if prev_b else 0.0
            p_ebit = prev_b.resultado_operativo if prev_b else 0.0
            p_eq = compute_metrics(prev_b).get('patrimonio_neto') if prev_b else None
            p_ven = prev_b.ventas if prev_b else 0.0

        actual_scoring = calculate_scoring(actual_metrics, p_res, p_eq, p_eb, p_ebit, p_ven)
    else:
        actual_scoring = {}

    comparativo_scoring = calculate_scoring(comparativo_metrics) if comparativo_balance else {}

    return render_template(
        "entry.html",
        companies=companies,
        selected_company=selected_company,
        actual_balance=actual_balance,
        comparativo_balance=comparativo_balance,
        selected_date=sel_date.isoformat() if sel_date else raw_selected_date,
        comparativo_date=comp_date.isoformat() if comp_date else raw_comparativo_date,
        available_dates=[b.fecha_balance for b in previous_balances],
        actual_metrics=actual_metrics,
        comparativo_metrics=comparativo_metrics,
        actual_scoring=actual_scoring,
        comparativo_scoring=comparativo_scoring,
        message=message,
        previous_balances=previous_balances,
    )

@app.route("/companies")
@login_required
def list_companies():
    try:
        # Forzamos un refresh para evitar datos cacheados
        session.expire_all()
        companies = session.query(EmpresaDB).order_by(EmpresaDB.razon_social).all()
        print(f"DEBUG WEB: Lista de Empresas - {len(companies)} encontradas", flush=True)
        return render_template("companies.html", companies=companies)
    except Exception as e:
        print(f"ERROR WEB en /companies: {str(e)}", flush=True)
        session.rollback()
        return render_template("companies.html", companies=[], error=str(e))


@app.route("/companies/new", methods=["GET", "POST"])
def company_form():
    if request.method == "POST":
        cuit = request.form.get("cuit", "").strip()
        try:
            if not cuit.isdigit() or len(cuit) != 11:
                raise ValueError("El CUIT debe tener exactamente 11 dígitos numéricos.")
            
            raw_anio_def = request.form.get("anio_default", "").strip()
            empresa = EmpresaDB(
                cuit=cuit,
                razon_social=request.form["razon_social"],
                actividad=request.form.get("actividad", ""),
                sector=request.form.get("sector", ""),
                anio_fundacion=request.form.get("anio_fundacion") or None,
                es_quebrada=1 if request.form.get("es_quebrada") else 0,
                en_default=1 if request.form.get("en_default") else 0,
                anio_default=int(raw_anio_def) if raw_anio_def.isdigit() else None,
                descripcion=request.form.get("descripcion", ""),
            )
            session.add(empresa)
            session.commit()
            return redirect(url_for("list_companies"))
        except (IntegrityError, ValueError) as e:
            session.rollback()
            msg = "Error: El CUIT ya existe." if isinstance(e, IntegrityError) else str(e)
            return render_template("company_form.html", message=msg)

    return render_template("company_form.html")


@app.route("/companies/<int:company_id>/edit", methods=["GET", "POST"])
def edit_company(company_id):
    empresa = session.get(EmpresaDB, company_id)
    if empresa is None:
        return redirect(url_for("list_companies"))

    if request.method == "POST":
        try:
            cuit = request.form.get("cuit", "").strip()
            if not cuit.isdigit() or len(cuit) != 11:
                raise ValueError("El CUIT debe tener exactamente 11 dígitos numéricos.")
            empresa.cuit = cuit
            empresa.razon_social = request.form["razon_social"]
            empresa.actividad = request.form.get("actividad", "")
            empresa.sector = request.form.get("sector", "")
            
            raw_anio_fund = request.form.get("anio_fundacion", "").strip()
            empresa.anio_fundacion = int(raw_anio_fund) if raw_anio_fund.isdigit() else None
            empresa.es_quebrada = 1 if request.form.get("es_quebrada") else 0
            empresa.en_default = 1 if request.form.get("en_default") else 0
            raw_anio_def = request.form.get("anio_default", "").strip()
            empresa.anio_default = int(raw_anio_def) if raw_anio_def.isdigit() else None
            empresa.descripcion = request.form.get("descripcion", "")
            session.commit()
            return redirect(url_for("list_companies"))
        except (IntegrityError, ValueError) as e:
            session.rollback()
            msg = "Error: El CUIT ya existe." if isinstance(e, IntegrityError) else str(e)
            return render_template("company_form.html", empresa=empresa, message=msg)

    return render_template("company_form.html", empresa=empresa)


@app.route("/companies/<int:company_id>/delete", methods=["POST"])
def delete_company(company_id):
    try:
        empresa = session.get(EmpresaDB, company_id)
        if empresa:
            session.delete(empresa)
            session.commit()
    except Exception:
        session.rollback()
    return redirect(url_for("list_companies"))


@app.route("/balances")
@login_required
def list_balances():
    try:
        session.expire_all()
        balances = session.query(EECCDB).all()
        print(f"DEBUG WEB: Lista de Balances - {len(balances)} encontrados", flush=True)
        return render_template("balances.html", balances=balances)
    except Exception as e:
        print(f"ERROR WEB en /balances: {str(e)}", flush=True)
        session.rollback()
        return render_template("balances.html", balances=[], error=str(e))


@app.route("/balances/new", methods=["GET", "POST"])
def balance_form():
    companies = session.query(EmpresaDB).all()
    if request.method == "POST":
        try:
            empresa_id = int(request.form["empresa_id"])
            empresa = session.get(EmpresaDB, empresa_id)
            balance = EECCDB(
                fecha_balance=parse_date(request.form["fecha_balance"]),
                anio=parse_date(request.form["fecha_balance"]).year if parse_date(request.form["fecha_balance"]) else 0,
                disponibilidades=parse_float(request.form.get("disponibilidades", 0.0)),
                activo_corriente=parse_float(request.form.get("activo_corriente", 0.0)),
                activo_no_corriente=parse_float(request.form.get("activo_no_corriente", 0.0)),
                pasivo_corriente=parse_float(request.form.get("pasivo_corriente", 0.0)),
                pasivo_no_corriente=parse_float(request.form.get("pasivo_no_corriente", 0.0)),
                deuda_financiera=parse_float(request.form.get("deuda_financiera", 0.0)),
                ventas=parse_float(request.form.get("ventas", 0.0)),
                resultado_operativo=parse_float(request.form.get("resultado_operativo", 0.0)),
                ebitda=parse_float(request.form.get("ebitda", 0.0)),
                resultado_neto=parse_float(request.form.get("resultado_neto", 0.0)),
                variacion_capital_trabajo=parse_float(request.form.get("variacion_capital_trabajo", 0.0)),
                flujo_caja_operativo=parse_float(request.form.get("flujo_caja_operativo", 0.0)),
                capex=parse_float(request.form.get("capex", 0.0)),
                dividendos=parse_float(request.form.get("dividendos", 0.0)),
                empresa=empresa,
            )
            session.add(balance)
            session.commit()
            return redirect(url_for("list_balances"))
        except Exception as e:
            session.rollback()
            return render_template("balance_form.html", companies=companies, message=f"Error: {str(e)}")

    return render_template("balance_form.html", companies=companies)


@app.route("/balances/<int:balance_id>/edit", methods=["GET", "POST"])
def edit_balance(balance_id):
    balance = session.get(EECCDB, balance_id)
    if balance is None:
        return redirect(url_for("list_balances"))

    companies = session.query(EmpresaDB).all()
    if request.method == "POST":
        try:
            balance.empresa_id = int(request.form["empresa_id"])
            balance.fecha_balance = parse_date(request.form["fecha_balance"])
            if balance.fecha_balance:
                balance.anio = balance.fecha_balance.year
            balance.disponibilidades = parse_float(request.form.get("disponibilidades", 0.0))
            balance.activo_corriente = parse_float(request.form.get("activo_corriente", 0.0))
            balance.activo_no_corriente = parse_float(request.form.get("activo_no_corriente", 0.0))
            balance.pasivo_corriente = parse_float(request.form.get("pasivo_corriente", 0.0))
            balance.pasivo_no_corriente = parse_float(request.form.get("pasivo_no_corriente", 0.0))
            balance.deuda_financiera = parse_float(request.form.get("deuda_financiera", 0.0))
            balance.ventas = parse_float(request.form.get("ventas", 0.0))
            balance.resultado_operativo = parse_float(request.form.get("resultado_operativo", 0.0))
            balance.ebitda = parse_float(request.form.get("ebitda", 0.0))
            balance.resultado_neto = parse_float(request.form.get("resultado_neto", 0.0))
            balance.flujo_caja_operativo = parse_float(request.form.get("flujo_caja_operativo", 0.0))
            balance.variacion_capital_trabajo = parse_float(request.form.get("variacion_capital_trabajo", 0.0))
            balance.capex = parse_float(request.form.get("capex", 0.0))
            balance.dividendos = parse_float(request.form.get("dividendos", 0.0))
            session.commit()
            return redirect(url_for("list_balances"))
        except Exception as e:
            session.rollback()
            return render_template("balance_form.html", balance=balance, companies=companies, message=f"Error: {str(e)}")

    return render_template("balance_form.html", balance=balance, companies=companies)


@app.route("/balances/<int:balance_id>/delete", methods=["POST"])
def delete_balance(balance_id):
    try:
        balance = session.get(EECCDB, balance_id)
        if balance:
            session.delete(balance)
            session.commit()
    except Exception:
        session.rollback()
    return redirect(url_for("list_balances"))


@app.route("/metrics")
def metrics():
    company_id = request.args.get("company_id", type=int)
    companies = session.query(EmpresaDB).all()
    if company_id:
        balances = session.query(EECCDB).filter_by(empresa_id=company_id).all()
    else:
        balances = session.query(EECCDB).all()

    metrics_data = [
        {"balance": balance, "metrics": compute_metrics(balance)} for balance in balances
    ]
    return render_template(
        "metrics.html",
        companies=companies,
        metrics_data=metrics_data,
        selected_company_id=company_id,
    )


@app.route("/export/csv")
def export_csv():
    balances = session.query(EECCDB).all()
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Headers para ML
    writer.writerow([
        "CUIT", "Empresa", "Sector", "Antiguedad_Años", "Target_Quebrada", "Target_Default", "Anio_Default",
        "Fecha_Balance", "Disponibilidades", "Activo_C", "Activo_NC", "Pasivo_C", "Pasivo_NC",
        "Ventas", "EBITDA", "Deuda_Fin", "Res_Neto", "ROE_%", "Liquidez_Ratio", "PN_Activo_%",
        "Score_Solvencia", "Score_Liquidez", "Score_Rentabilidad", "Score_Nosis", "Score_Total",
        "FCO", "Variacion_WC", "FGO", "Capex", "Dividendos"
    ])
    
    for b in balances:
        m = compute_metrics(b)
        writer.writerow([
            b.empresa.cuit, b.empresa.razon_social, b.empresa.sector, 
            m['otros']['Antigüedad (años)'], b.empresa.es_quebrada, b.empresa.en_default, b.empresa.anio_default,
            b.fecha_balance, b.disponibilidades, b.activo_corriente, b.activo_no_corriente,
            b.pasivo_corriente, b.pasivo_no_corriente, b.ventas, b.ebitda, b.deuda_financiera,
            b.resultado_neto, m['rentabilidad']['ROE'], m['liquidez']['Liquidez'], 
            m['solvencia']['PN/Activos'], b.score_solvencia, b.score_liquidez,
            b.score_rentabilidad, b.score_nosis, b.score_total,
            b.flujo_caja_operativo, b.variacion_capital_trabajo, m['flujo_de_caja']['Flujo Generado por Operaciones (FGO)'],
            b.capex, b.dividendos
        ])
    
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=database_ml_riesgo.csv"
    response.headers["Content-type"] = "text/csv"
    return response

@app.route("/stats")
def stats():
    # Subconsulta para obtener la fecha del último balance por empresa
    subquery = session.query(
        EECCDB.empresa_id,
        func.max(EECCDB.fecha_balance).label('max_date')
    ).group_by(EECCDB.empresa_id).subquery()

    # Consulta principal: contar empresas por su etiqueta de score más reciente
    query_results = session.query(
        EECCDB.score_label,
        func.count(EECCDB.id)
    ).join(
        subquery,
        (EECCDB.empresa_id == subquery.c.empresa_id) & (EECCDB.fecha_balance == subquery.c.max_date)
    ).group_by(EECCDB.score_label).all()

    # Convertir a diccionario y asegurar orden jerárquico
    order = ["A (Excelente)", "B (Muy Bueno)", "C (Aceptable)", "D"]
    raw_stats = {label: count for label, count in query_results if label}
    
    stats_summary = []
    for label in order:
        if label in raw_stats:
            stats_summary.append({'label': label, 'count': raw_stats[label]})

    # Estadísticas de Default/Quiebra para el proyecto de ML
    target_stats = session.query(
        func.sum(EmpresaDB.es_quebrada).label('quebradas'),
        func.sum(EmpresaDB.en_default).label('defaults')
    ).first()

    return render_template("stats.html", stats=stats_summary, target_stats=target_stats)

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ("true", "1", "t")
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
