from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, create_engine, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

class EmpresaDB(Base):
    __tablename__ = 'empresas'
    
    id = Column(Integer, primary_key=True)
    cuit = Column(String, unique=True, nullable=False)
    razon_social = Column(String, nullable=False)
    actividad = Column(String)
    anio_fundacion = Column(Integer)
    descripcion = Column(Text)
    
    # Relación 1 a N con los Estados Contables
    balances = relationship("EECCDB", back_populates="empresa", cascade="all, delete-orphan")

class EECCDB(Base):
    __tablename__ = 'estados_contables'
    
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey('empresas.id'))
    anio = Column(Integer, nullable=False)
    
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

    with engine.connect() as conn:
        result = conn.execute(text('PRAGMA table_info(estados_contables)'))
        existing_columns = {row[1] for row in result.fetchall()}
        required_columns = {
            'disponibilidades': 'FLOAT',
            'activo_no_corriente': 'FLOAT',
            'pasivo_no_corriente': 'FLOAT',
            'ventas': 'FLOAT',
            'resultado_operativo': 'FLOAT',
            'resultado_neto': 'FLOAT',
            'flujo_caja_operativo': 'FLOAT',
            'variacion_capital_trabajo': 'FLOAT',
            'capex': 'FLOAT',
            'dividendos': 'FLOAT',
        }

        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                conn.execute(
                    text(
                        f'ALTER TABLE estados_contables ADD COLUMN {column_name} {column_type} DEFAULT 0.0'
                    )
                )
        conn.commit()

_ensure_sqlite_schema(engine)

# Sesión para interactuar con la base de datos
Session = sessionmaker(bind=engine)
session = Session()