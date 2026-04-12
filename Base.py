from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, Date, create_engine, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session

Base = declarative_base()

class EmpresaDB(Base):
    __tablename__ = 'empresas'
    
    id = Column(Integer, primary_key=True)
    cuit = Column(String, unique=True, nullable=False)
    razon_social = Column(String, nullable=False)
    actividad = Column(String)
    sector = Column(String)
    anio_fundacion = Column(Integer)
    descripcion = Column(Text)
    
    # Relación 1 a N con los Estados Contables
    balances = relationship("EECCDB", back_populates="empresa", cascade="all, delete-orphan")

class EECCDB(Base):
    __tablename__ = 'estados_contables'
    
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey('empresas.id'))
    fecha_balance = Column(Date, nullable=False)
    anio = Column(Integer, nullable=False, default=0)
    
    # Cuentas del Balance General
    disponibilidades = Column(Float, default=0.0)
    activo_corriente = Column(Float, default=0.0)
    activo_no_corriente = Column(Float, default=0.0)
    pasivo_corriente = Column(Float, default=0.0)
    pasivo_no_corriente = Column(Float, default=0.0)
    deuda_financiera = Column(Float, default=0.0)
    ventas = Column(Float, default=0.0)
    resultado_operativo = Column(Float, default=0.0)
    ebitda = Column(Float, default=0.0)
    resultado_neto = Column(Float, default=0.0)
    flujo_caja_operativo = Column(Float, default=0.0)
    variacion_capital_trabajo = Column(Float, default=0.0)
    capex = Column(Float, default=0.0)
    dividendos = Column(Float, default=0.0)
    analisis = Column(Text)
    # ... (resto de las métricas contables) ...

    # Relación inversa
    empresa = relationship("EmpresaDB", back_populates="balances")

# Configuración del motor de base de datos (SQLite para desarrollo local)
engine = create_engine('sqlite:///riesgo_caucion.db')
Base.metadata.create_all(engine)

# Asegura que la tabla de estados contables tenga todas las columnas requeridas
# cuando la base de datos ya existe con un esquema anterior.
def _ensure_sqlite_schema(engine):
    if engine.dialect.name != 'sqlite':
        return

    try:
        with engine.begin() as conn:
            # Check empresas table for 'sector' column
            result = conn.execute(text('PRAGMA table_info(empresas)'))
            existing_columns_emp = {row[1] for row in result.fetchall()}
            if existing_columns_emp and 'sector' not in existing_columns_emp:
                conn.execute(text("ALTER TABLE empresas ADD COLUMN sector TEXT DEFAULT ''"))

            result = conn.execute(text('PRAGMA table_info(estados_contables)'))
            existing_columns = {row[1] for row in result.fetchall()}
            
            if existing_columns:
                required_columns = {
                    'fecha_balance': 'DATE',
                    'anio': 'INTEGER',
                    'disponibilidades': 'FLOAT',
                    'activo_corriente': 'FLOAT',
                    'activo_no_corriente': 'FLOAT',
                    'pasivo_corriente': 'FLOAT',
                    'pasivo_no_corriente': 'FLOAT',
                    'deuda_financiera': 'FLOAT',
                    'ebitda': 'FLOAT',
                    'ventas': 'FLOAT',
                    'resultado_operativo': 'FLOAT',
                    'resultado_neto': 'FLOAT',
                    'flujo_caja_operativo': 'FLOAT',
                    'variacion_capital_trabajo': 'FLOAT',
                    'capex': 'FLOAT',
                    'dividendos': 'FLOAT',
                    'analisis': 'TEXT',
                }

                for column_name, column_type in required_columns.items():
                    if column_name not in existing_columns:
                        # Asignar un valor por defecto adecuado según el tipo de dato para SQLite
                        if column_type == 'DATE':
                            default_val = "'2000-01-01'"
                        elif column_type == 'TEXT':
                            default_val = "''"
                        elif column_type == 'INTEGER':
                            default_val = "0"
                        else:
                            default_val = "0.0"

                        conn.execute(
                            text(
                                f'ALTER TABLE estados_contables ADD COLUMN {column_name} {column_type} DEFAULT {default_val}'
                            )
                        )
                # Corregir registros existentes que tengan el valor numérico 0.0 en la columna de fecha
                if 'fecha_balance' in existing_columns or 'fecha_balance' in required_columns:
                    conn.execute(
                        text("UPDATE estados_contables SET fecha_balance = '2000-01-01' WHERE fecha_balance = 0.0")
                    )
    except Exception as e:
        print(f"Error durante la migración de esquema: {e}")

_ensure_sqlite_schema(engine)

# Sesión para interactuar con la base de datos
Session = sessionmaker(bind=engine)
session = scoped_session(Session)