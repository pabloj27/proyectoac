from datetime import date, datetime

from flask import Flask, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from Base import EECCDB, EmpresaDB, session

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
        "PN/Activos": round(safe_division(patrimonio_neto, total_activo), 2),
        "Deuda Financiera/Activos": round(safe_division(deuda_financiera, total_activo), 2),
        "Deuda Financiera/EBITDA": round(safe_division(deuda_financiera, ebitda), 2),
    }

    liquidez = {
        "Capital de Trabajo": round(capital_trabajo, 2),
        "Liquidez": round(safe_division(balance.activo_corriente, pasivo_corriente), 2),
        "Disponibilidades/Deuda Financiera": round(safe_division(balance.disponibilidades, deuda_financiera), 2),
        "Capital de Trabajo / Ventas": round(safe_division(capital_trabajo, ventas), 2),
    }

    flujo_caja = {
        "Flujo Generado / Ventas": round(safe_division(flujo_generado, ventas), 2),
        "Flujo Operativo / Ventas": round(safe_division(balance.flujo_caja_operativo, ventas), 2),
        "Flujo Libre / Ventas": round(safe_division(flujo_libre, ventas), 2),
    }

    rentabilidad = {
        "ROE": round(safe_division(balance.resultado_neto, patrimonio_neto), 2),
        "EBITDA%": round(safe_division(balance.ebitda, ventas), 2),
    }

    return {
        "solvencia": solvencia,
        "liquidez": liquidez,
        "flujo_de_caja": flujo_caja,
        "rentabilidad": rentabilidad,
        "values": {
            "Flujo generado": round(flujo_generado, 2),
            "Flujo libre": round(flujo_libre, 2),
        },
    }

# Configuración de umbrales para el Scoring
SCORING_THRESHOLDS = {
    'solvencia_pn_activos': {
        'high': {'limit': 0.40, 'points': 5.0},
        'mid':  {'limit': 0.20, 'points': 2.5}
    }
}

def calculate_scoring(metrics: dict) -> dict:
    if not metrics: return {}
    
    # --- 1. LIQUIDEZ (Peso 40%) ---
    liq_val = metrics['liquidez']['Liquidez']
    ct_ventas = metrics['liquidez']['Capital de Trabajo / Ventas']
    
    s_liq = 0
    s_liq += 7.0 if liq_val > 1.5 else (4.0 if liq_val > 1.2 else (1.5 if liq_val > 1.0 else 0))
    s_liq += 3.0 if ct_ventas > 0.15 else (1.5 if ct_ventas > 0.05 else 0)
    s_liq = min(10.0, s_liq)  # Ajuste para no exceder la escala 1-10 por categoría
    
    # --- 2. SOLVENCIA (Peso 30%) ---
    pn_activos = metrics['solvencia']['PN/Activos']
    deuda_ebitda = metrics['solvencia']['Deuda Financiera/EBITDA']
    
    s_sol = 0
    # Evaluación de PN / Activos usando la configuración
    conf_pn = SCORING_THRESHOLDS['solvencia_pn_activos']
    if pn_activos > conf_pn['high']['limit']:
        s_sol += conf_pn['high']['points']
    elif pn_activos > conf_pn['mid']['limit']:
        s_sol += conf_pn['mid']['points']

    # Nota: Deuda/EBITDA es mejor cuanto más bajo sea
    if deuda_ebitda > 0:
        s_sol += 5 if deuda_ebitda < 2.5 else (2.5 if deuda_ebitda < 4.5 else 0)
    else: s_sol += 5 # Sin deuda financiera o EBITDA negativo (se asume deuda 0 si no hay valor)

    # --- 3. RENTABILIDAD Y FLUJO (Peso 30%) ---
    roe = metrics['rentabilidad']['ROE']
    ebitda_pct = metrics['rentabilidad']['EBITDA%']
    fcf_ventas = metrics['flujo_de_caja']['Flujo Libre / Ventas']
    
    s_rent = 0
    s_rent += 4 if ebitda_pct > 0.15 else (2 if ebitda_pct > 0.07 else 0)
    s_rent += 3 if roe > 0.15 else (1.5 if roe > 0.05 else 0)
    s_rent += 3 if fcf_ventas > 0.05 else (1.5 if fcf_ventas > 0 else 0)

    # Ponderación Final (Escala 1-10)
    final_score = (s_liq * 0.40) + (s_sol * 0.30) + (s_rent * 0.30)
    
    label = "C"
    color = "#dc3545" # Rojo
    if final_score >= 8.5: 
        label = "AAA (Excelente)"
        color = "#198754"
    elif final_score >= 7.0: 
        label = "AA (Muy Bueno)"
        color = "#20c997"
    elif final_score >= 5.0: 
        label = "A (Aceptable)"
        color = "#ffc107"
    
    return {
        "liquidez": round(s_liq, 1),
        "solvencia": round(s_sol, 1),
        "rentabilidad": round(s_rent, 1),
        "total": round(final_score, 1),
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

    # Obtenemos las fechas de los argumentos de la URL (para recarga por búsqueda)
    selected_date_str = request.args.get("selected_date")
    comparativo_date_str = request.args.get("comparativo_date")
    
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
        actual_metrics = compute_metrics(actual_balance) if actual_balance else {}
        comparativo_metrics = compute_metrics(comparativo_balance) if comparativo_balance else {}
        actual_scoring = calculate_scoring(actual_metrics)
        comparativo_scoring = calculate_scoring(comparativo_metrics)
        return render_template(
            "entry.html",
            companies=companies,
            selected_company=selected_company,
            actual_balance=actual_balance,
            comparativo_balance=comparativo_balance,
            selected_date=selected_date_str,
            comparativo_date=comparativo_date_str,
            available_dates=available_dates,
            actual_metrics=actual_metrics,
            comparativo_metrics=comparativo_metrics,
            actual_scoring=actual_scoring,
            comparativo_scoring=comparativo_scoring,
            message=message,
            previous_balances=previous_balances,
        )

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
            comparativo_balance.capex = parse_float(request.form.get("comparativo_capex", "0.0"))
            comparativo_balance.dividendos = parse_float(request.form.get("comparativo_dividendos", "0.0"))
            session.add(comparativo_balance)

        session.commit()
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

    previous_balances = (
        session.query(EECCDB)
        .filter_by(empresa_id=selected_company.id)
        .order_by(EECCDB.fecha_balance.desc())
        .all()
    )

    actual_metrics = compute_metrics(actual_balance) if actual_balance else {}
    comparativo_metrics = compute_metrics(comparativo_balance) if comparativo_balance else {}
    actual_scoring = calculate_scoring(actual_metrics)
    comparativo_scoring = calculate_scoring(comparativo_metrics)

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
            empresa = EmpresaDB(
                cuit=cuit,
                razon_social=request.form["razon_social"],
                actividad=request.form.get("actividad", ""),
                sector=request.form.get("sector", ""),
                anio_fundacion=request.form.get("anio_fundacion") or None,
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
            empresa.anio_fundacion = request.form.get("anio_fundacion") or None
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


@app.route("/help")
def help_page():
    return render_template("help.html")


if __name__ == "__main__":
    app.run(debug=True)
