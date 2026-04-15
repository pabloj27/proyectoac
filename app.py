import csv
import math
import io
import os
from datetime import date, datetime

from flask import Flask, redirect, render_template, request, url_for, make_response, jsonify
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv

from Base import EECCDB, EmpresaDB, session

# Cargar variables de entorno desde el archivo .env
load_dotenv()

app = Flask(__name__)

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
    flujo_generado = balance.flujo_caja_operativo + balance.variacion_capital_trabajo
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
        "Disponibilidades/Deuda Financiera": round(safe_division(balance.disponibilidades, deuda_financiera), 2),
        "Capital de Trabajo / Ventas": round(safe_division(capital_trabajo, ventas) * 100, 2),
    }

    flujo_caja = {
        "Flujo Generado / Ventas": round(safe_division(flujo_generado, ventas) * 100, 2),
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
        "flujo_caja_operativo_raw": balance.flujo_caja_operativo,
        "otros": {
            "Antigüedad (años)": date.today().year - (balance.empresa.anio_fundacion or date.today().year),
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

def calculate_scoring(metrics: dict, prev_result: float = 0.0, prev_equity: float = None, prev_ebitda: float = 0.0) -> dict:
    if not metrics: return {}
    
    # --- 1. LIQUIDEZ (Peso 40%) ---
    liq_val = metrics['liquidez']['Liquidez']
    ct_ventas = metrics['liquidez']['Capital de Trabajo / Ventas']
    
    s_liq = 0
    s_liq += 7.0 if liq_val > 1.5 else (4.0 if liq_val > 1.1 else (1.5 if liq_val > 1.0 else 0))
    s_liq += 3.0 if ct_ventas > 10.0 else (1.5 if ct_ventas > 5.0 else 0)
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

    # --- 4. ANTIGÜEDAD (Peso 10%) ---
    ant = metrics['otros']['Antigüedad (años)']
    s_age = 10.0 if ant >= 30 else (7.0 if ant >= 10 else (4.0 if ant >= 5 else 0))

    # --- 5. NOSIS (Peso 15%) ---
    nos = metrics['otros']['Score NOSIS']
    s_nos = nos / 100.0

    # Ponderación Final (Escala 1-10)
    final_score = (s_liq * 0.30) + (s_sol * 0.25) + (s_rent * 0.20) + (s_age * 0.10) + (s_nos * 0.15)
    
    # --- EVALUACIÓN DE HARD STOPS (No afectan el puntaje numérico) ---
    has_hard_stop = False
    
    # 1. Pérdida > 80% del Patrimonio Neto
    if prev_equity is not None and prev_equity > 0:
        pn_actual = metrics.get('patrimonio_neto', 0.0)
        if pn_actual < (prev_equity * 0.20):
            has_hard_stop = True

    # 2. EBITDA Negativo Significativo vs PN (> 50%)
    ebitda_act = metrics.get('ebitda_raw', 0.0)
    pn_act = metrics.get('patrimonio_neto', 0.0)
    if pn_act > 0 and safe_division(ebitda_act + prev_ebitda, pn_act) < -0.5:
            has_hard_stop = True

    # 3. Score NOSIS menor a 200
    if 0 < nos < 200:
        has_hard_stop = True

    # 4. Liquidez Operativa Crítica (CT + FCO < 0)
    ct = metrics['liquidez']['Capital de Trabajo']
    fco = metrics.get('flujo_caja_operativo_raw', 0.0)
    if (ct + fco) < 0:
        has_hard_stop = True

    display_score = round(final_score, 1)
    label = "D"
    color = "#dc3545" # Rojo
    
    if not has_hard_stop:
        if display_score >= 8.5: 
            label = "A (Excelente)"
            color = "#198754"
        elif display_score >= 7.0: 
            label = "B (Muy Bueno)"
            color = "#20c997"
        elif display_score >= 5.0: 
            label = "C (Aceptable)"
            color = "#ffc107"
    else:
        label = "D (Rechazo por Hard Stop)"

    # Cálculo de Cupo Sugerido basado en escalas y PN Actual
    pn_actual = metrics.get('patrimonio_neto', 0.0)
    if has_hard_stop:
        cupo_sugerido, multiplo = 0.0, 0.0
    else:
        cupo_sugerido, multiplo = calculate_suggested_limit(pn_actual, display_score)
    
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
        "total": display_score,
        "cupo_sugerido": cupo_sugerido,
        "pn_actual": pn_actual,
        "multiplo": multiplo,
        "label": label,
        "color": color
    }

def variation_label(actual: float, comparativo: float) -> str:
    if actual > comparativo:
        return "Mejora"
    if actual < comparativo:
        return "Empeora"
    return "Sin cambio"


@app.route("/")
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
    cuit = data.get("cuit")
    sector = data.get("sector")
    
    api_key = os.environ.get("GOOGLE_API_KEY") 
    
    if not api_key:
        return jsonify({"error": "No se encontró la GOOGLE_API_KEY en el archivo .env"}), 400
    
    if not razon_social:
        return jsonify({"error": "Debe proporcionar la Razón Social de la empresa para buscar información."}), 400

    try:
        # Inicializamos el modelo de Gemini
        llm = ChatGoogleGenerativeAI(
            model="gemini-3-flash-preview",
            google_api_key=api_key,
            temperature=0.1,
            tools=[{"google_search_retrieval": {}}]
        )

        # Definimos el Prompt Template según lo solicitado
        prompt = ChatPromptTemplate.from_template("""
            Actúa como un experto analista de riesgo crediticio con acceso a búsqueda en tiempo real.

            Empresa: {empresa}
            CUIT: {cuit}
            Sector Informado: {sector}

            INSTRUCCIONES CRÍTICAS:
            1. UTILIZA GOOGLE SEARCH para buscar información actualizada sobre esta empresa (CUIT y Nombre) en registros públicos, sitios financieros y noticias de Argentina.
            2. Si NO tienes información específica y verificable de esta empresa, NO inventes datos (ubicación, socios, etc.). 
               En su lugar, describe el perfil operativo y los riesgos típicos para el sector "{sector}" en el contexto económico actual.
            3. Mantén un tono estrictamente profesional y técnico.

            Genera una descripción objetiva incluyendo:

            - Actividad principal
            - Grupo empresarial (si aplica)
            - Ubicación donde opera principalmente
            - Noticias recientes sobre la empresa(si no hay info, indicar "Sin información pública reciente")

            Formato:
            - Máximo 120 palabras
            - Lenguaje técnico
            - Sin opiniones ni suposiciones
            """)
        
        
        from langchain_core.output_parsers import StrOutputParser
        try:
            chain = prompt | llm | StrOutputParser()
            response = chain.invoke({"empresa": razon_social, "cuit": cuit, "sector": sector})
            
            # Al usar StrOutputParser, 'response' ya es un string, no tiene atributo '.content'
            if response:
                return jsonify({"description": response})
            return jsonify({"error": "La IA no devolvió contenido."}), 500
            
        except Exception as e:
            return jsonify({"error": f"Error al invocar el modelo: {str(e)}"}), 500

    except Exception as e:
        return jsonify({"error": f"Error de configuración: {str(e)}"}), 500



@app.route("/entry", methods=["GET", "POST"])
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
                actual_nosis = int(parse_float(request.form.get("actual_nosis_score", "0")))
                if actual_nosis > 999:
                    raise ValueError("El Score NOSIS actual no puede superar los 999 puntos.")
                actual_balance.nosis_score = actual_nosis
                actual_balance.capex = parse_float(request.form.get("actual_capex", "0.0"))
                actual_balance.dividendos = parse_float(request.form.get("actual_dividendos", "0.0"))
                actual_balance.analisis = request.form.get("actual_analisis", "")
                
                # Persistir Scoring para ML
                m_act = compute_metrics(actual_balance)
                res_ant = comparativo_balance.resultado_neto if comparativo_balance else 0.0
                eb_ant = comparativo_balance.ebitda if comparativo_balance else 0.0
                m_comp_temp = compute_metrics(comparativo_balance) if comparativo_balance else {}
                pn_ant = m_comp_temp.get('patrimonio_neto')
                sc_act = calculate_scoring(m_act, res_ant, pn_ant, eb_ant)
                actual_balance.score_solvencia = sc_act['solvencia']
                actual_balance.score_liquidez = sc_act['liquidez']
                actual_balance.score_rentabilidad = sc_act['rentabilidad']
                actual_balance.score_antiguedad = sc_act['antiguedad']
                actual_balance.score_nosis = sc_act['nosis']
                actual_balance.score_total = sc_act['total']
                actual_balance.score_label = sc_act['label']
                actual_balance.cupo_sugerido = sc_act['cupo_sugerido']
                actual_balance.multiplo_sugerido = sc_act['multiplo']
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
                comp_nosis = int(parse_float(request.form.get("comparativo_nosis_score", "0")))
                if comp_nosis > 999:
                    raise ValueError("El Score NOSIS comparativo no puede superar los 999 puntos.")
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
        prev_b = session.query(EECCDB).filter(
            EECCDB.empresa_id == selected_company.id, 
            EECCDB.fecha_balance < actual_balance.fecha_balance
        ).order_by(EECCDB.fecha_balance.desc()).first()
        
        prev_res = prev_b.resultado_neto if prev_b else 0.0
        prev_eb = prev_b.ebitda if prev_b else 0.0
        prev_eq = compute_metrics(prev_b).get('patrimonio_neto') if prev_b else None
        
        actual_scoring = calculate_scoring(actual_metrics, prev_res, prev_eq, prev_eb)
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
def list_companies():
    companies = session.query(EmpresaDB).order_by(EmpresaDB.razon_social).all()
    return render_template("companies.html", companies=companies)


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
def list_balances():
    try:
        balances = session.query(EECCDB).all()
        return render_template("balances.html", balances=balances)
    except Exception:
        session.rollback()
        return render_template("balances.html", balances=[])


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
                disponibilidades=float(request.form.get("disponibilidades", 0.0)),
                activo_corriente=float(request.form.get("activo_corriente", 0.0)),
                activo_no_corriente=float(request.form.get("activo_no_corriente", 0.0)),
                pasivo_corriente=float(request.form.get("pasivo_corriente", 0.0)),
                pasivo_no_corriente=float(request.form.get("pasivo_no_corriente", 0.0)),
                deuda_financiera=float(request.form.get("deuda_financiera", 0.0)),
                ventas=float(request.form.get("ventas", 0.0)),
                resultado_operativo=float(request.form.get("resultado_operativo", 0.0)),
                ebitda=float(request.form.get("ebitda", 0.0)),
                resultado_neto=float(request.form.get("resultado_neto", 0.0)),
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
            balance.disponibilidades = float(request.form.get("disponibilidades", 0.0))
            balance.activo_corriente = float(request.form.get("activo_corriente", 0.0))
            balance.activo_no_corriente = float(request.form.get("activo_no_corriente", 0.0))
            balance.pasivo_corriente = float(request.form.get("pasivo_corriente", 0.0))
            balance.pasivo_no_corriente = float(request.form.get("pasivo_no_corriente", 0.0))
            balance.deuda_financiera = float(request.form.get("deuda_financiera", 0.0))
            balance.ventas = float(request.form.get("ventas", 0.0))
            balance.resultado_operativo = float(request.form.get("resultado_operativo", 0.0))
            balance.ebitda = float(request.form.get("ebitda", 0.0))
            balance.resultado_neto = float(request.form.get("resultado_neto", 0.0))
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
        "Score_Solvencia", "Score_Liquidez", "Score_Rentabilidad", "Score_Nosis", "Score_Total"
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
            b.score_rentabilidad, b.score_nosis, b.score_total
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
    app.run(debug=True)
