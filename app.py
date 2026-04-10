from datetime import date

from flask import Flask, redirect, render_template, request, url_for

from Base import EECCDB, EmpresaDB, session

app = Flask(__name__)

@app.template_filter('format_es')
def format_es(value, decimals=2):
    if value is None:
        return "0,00"
    try:
        format_str = "{:,." + str(decimals) + "f}"
        return format_str.format(float(value)).replace(',', 'X').replace('.', ',').replace('X', '.')
    except (ValueError, TypeError):
        return value


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
    sanitized = str(value).replace('.', '').replace(',', '.')
    try:
        return float(sanitized)
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

    solvencia = {
        "PN/Activos": round(safe_division(activo_total - (balance.pasivo_corriente + balance.pasivo_no_corriente), total_activo), 2),
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

    return {
        "solvencia": solvencia,
        "liquidez": liquidez,
        "flujo_de_caja": flujo_caja,
        "values": {
            "Flujo generado": round(flujo_generado, 2),
            "Flujo libre": round(flujo_libre, 2),
        },
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
    companies = session.query(EmpresaDB).order_by(EmpresaDB.razon_social).all()
    today_year = date.today().year
    selected_company = None
    actual_balance = None
    comparativo_balance = None
    results = None
    message = None
    previous_balances = []

    selected_year = request.args.get("selected_year", type=int) or today_year
    comparativo_year = request.args.get("comparativo_year", type=int) or (selected_year - 1)

    if request.method == "GET":
        company_id = request.args.get("company_id", type=int)
        if company_id:
            selected_company = session.get(EmpresaDB, company_id)
            if selected_company:
                previous_balances = (
                    session.query(EECCDB)
                    .filter_by(empresa_id=company_id)
                    .order_by(EECCDB.anio.desc())
                    .all()
                )
                actual_balance = (
                    session.query(EECCDB)
                    .filter_by(empresa_id=company_id, anio=selected_year)
                    .first()
                )
                comparativo_balance = (
                    session.query(EECCDB)
                    .filter_by(empresa_id=company_id, anio=comparativo_year)
                    .first()
                )
        actual_metrics = compute_metrics(actual_balance) if actual_balance else {}
        comparativo_metrics = compute_metrics(comparativo_balance) if comparativo_balance else {}
        return render_template(
            "entry.html",
            companies=companies,
            selected_company=selected_company,
            actual_balance=actual_balance,
            comparativo_balance=comparativo_balance,
            selected_year=selected_year,
            comparativo_year=comparativo_year,
            actual_metrics=actual_metrics,
            comparativo_metrics=comparativo_metrics,
            message=message,
            previous_balances=previous_balances,
        )

    company_id = request.form.get("company_id")
    if company_id:
        selected_company = session.get(EmpresaDB, int(company_id))

    cuit = request.form.get("cuit", "").strip()
    if selected_company is None and cuit:
        selected_company = session.query(EmpresaDB).filter_by(cuit=cuit).first()

    if selected_company is None:
        selected_company = EmpresaDB(
            cuit=cuit,
            razon_social=request.form.get("razon_social", ""),
            actividad=request.form.get("actividad", ""),
            anio_fundacion=request.form.get("anio_fundacion") or None,
            descripcion=request.form.get("descripcion", ""),
        )
        session.add(selected_company)
        session.flush()
        message = "Empresa nueva guardada."
    else:
        selected_company.cuit = cuit
        selected_company.razon_social = request.form.get("razon_social", "")
        selected_company.actividad = request.form.get("actividad", "")
        selected_company.anio_fundacion = request.form.get("anio_fundacion") or None
        selected_company.descripcion = request.form.get("descripcion", "")
        message = "Empresa existente actualizada."

    session.commit()

    selected_year = int(request.form.get("selected_year", today_year))
    comparativo_year = int(request.form.get("comparativo_year", selected_year - 1))

    actual_balance = (
        session.query(EECCDB)
        .filter_by(empresa_id=selected_company.id, anio=selected_year)
        .first()
    )
    if actual_balance is None:
        actual_balance = EECCDB(anio=selected_year, empresa=selected_company)
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
    session.add(actual_balance)

    comparativo_balance = (
        session.query(EECCDB)
        .filter_by(empresa_id=selected_company.id, anio=comparativo_year)
        .first()
    )
    if comparativo_balance is None:
        comparativo_balance = EECCDB(anio=comparativo_year, empresa=selected_company)
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

    previous_balances = (
        session.query(EECCDB)
        .filter_by(empresa_id=selected_company.id)
        .order_by(EECCDB.anio.desc())
        .all()
    )

    actual_metrics = compute_metrics(actual_balance)
    comparativo_metrics = compute_metrics(comparativo_balance)

    return render_template(
        "entry.html",
        companies=companies,
        selected_company=selected_company,
        actual_balance=actual_balance,
        comparativo_balance=comparativo_balance,
        selected_year=selected_year,
        comparativo_year=comparativo_year,
        actual_metrics=actual_metrics,
        comparativo_metrics=comparativo_metrics,
        message=message,
        previous_balances=previous_balances,
    )

@app.route("/companies/new", methods=["GET", "POST"])
def company_form():
    if request.method == "POST":
        empresa = EmpresaDB(
            cuit=request.form["cuit"],
            razon_social=request.form["razon_social"],
            actividad=request.form.get("actividad", ""),
            anio_fundacion=request.form.get("anio_fundacion") or None,
            descripcion=request.form.get("descripcion", ""),
        )
        session.add(empresa)
        session.commit()
        return redirect(url_for("list_companies"))

    return render_template("company_form.html")


@app.route("/companies/<int:company_id>/edit", methods=["GET", "POST"])
def edit_company(company_id):
    empresa = session.get(EmpresaDB, company_id)
    if empresa is None:
        return redirect(url_for("list_companies"))

    if request.method == "POST":
        empresa.cuit = request.form["cuit"]
        empresa.razon_social = request.form["razon_social"]
        empresa.actividad = request.form.get("actividad", "")
        empresa.anio_fundacion = request.form.get("anio_fundacion") or None
        empresa.descripcion = request.form.get("descripcion", "")
        session.commit()
        return redirect(url_for("list_companies"))

    return render_template("company_form.html", empresa=empresa)


@app.route("/companies/<int:company_id>/delete", methods=["POST"])
def delete_company(company_id):
    empresa = session.get(EmpresaDB, company_id)
    if empresa:
        session.delete(empresa)
        session.commit()
    return redirect(url_for("list_companies"))


@app.route("/balances")
def list_balances():
    balances = session.query(EECCDB).all()
    return render_template("balances.html", balances=balances)


@app.route("/balances/new", methods=["GET", "POST"])
def balance_form():
    companies = session.query(EmpresaDB).all()
    if request.method == "POST":
        empresa_id = int(request.form["empresa_id"])
        empresa = session.get(EmpresaDB, empresa_id)
        balance = EECCDB(
            anio=int(request.form["anio"]),
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

    return render_template("balance_form.html", companies=companies)


@app.route("/balances/<int:balance_id>/edit", methods=["GET", "POST"])
def edit_balance(balance_id):
    balance = session.get(EECCDB, balance_id)
    if balance is None:
        return redirect(url_for("list_balances"))

    companies = session.query(EmpresaDB).all()
    if request.method == "POST":
        balance.empresa_id = int(request.form["empresa_id"])
        balance.anio = int(request.form["anio"])
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

    return render_template("balance_form.html", balance=balance, companies=companies)


@app.route("/balances/<int:balance_id>/delete", methods=["POST"])
def delete_balance(balance_id):
    balance = session.get(EECCDB, balance_id)
    if balance:
        session.delete(balance)
        session.commit()
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


if __name__ == "__main__":
    app.run(debug=True)
