"""
Donde vive el diario: Postgres si hay DATABASE_URL, SQLite si no.

POR QUE DOS
───────────
El forward test del capitulo 20.1 dura «1-3 meses». En Render el disco es
efimero: cada despliegue arranca un contenedor nuevo y lo que hubiera en
un archivo desaparece. Un diario en SQLite alli perderia las operaciones
en papel justo cuando empiezan a ser una muestra, y el paso 7 no se
podria completar nunca.

En una maquina local pasa lo contrario: pedir credenciales de una base
remota para correr un backtest es una barrera sin motivo, y si la red
falla el backtest falla con ella.

Asi que el destino lo decide el entorno. Con `DATABASE_URL` va a
Postgres; sin ella, a un archivo. La misma API en los dos casos, para que
ni `diario.py` ni nadie tenga que preguntar donde esta escribiendo.

LAS TRES DIFERENCIAS QUE HAY QUE TRADUCIR
    marcador       ?            vs  %s
    autonumerico   AUTOINCREMENT vs  GENERATED ... AS IDENTITY
    id del insert  lastrowid    vs  RETURNING id

Son pocas y estan todas aqui. Repartirlas por el codigo que consulta es
como se acaba con dos dialectos de SQL mezclados y consultas que
funcionan en una maquina y no en la otra.

SINCRONO A PROPOSITO
El resto del repo usa `asyncpg`. Aqui se usa `psycopg`, que es sincrono,
porque el diario se llama desde seis sitios que no son corrutinas y
volverlos async arrastraria hasta los manejadores HTTP. El coste de
bloquear el bucle es real pero minusculo: son unas pocas consultas cada
cinco minutos, no una por peticion.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path

RUTA_SQLITE = Path(__file__).resolve().parent.parent / "state" / "diario_materias.db"
ESQUEMA_PG = os.getenv("MATERIAS_ESQUEMA", "materias")

log = logging.getLogger("materias.almacen")
_local = threading.local()


def usa_postgres() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def destino() -> str:
    """Donde se esta escribiendo, para poder decirlo en el log y en /salud."""
    if not usa_postgres():
        return f"sqlite:{RUTA_SQLITE}"
    dsn = os.getenv("DATABASE_URL", "")
    # Nunca el usuario ni la contrasena: esto acaba en logs y en /salud.
    m = re.search(r"@([^/?]+)", dsn)
    return f"postgres:{m.group(1) if m else '?'}/{ESQUEMA_PG}"


# ── El esquema, en los dos dialectos ──────────────────────────────────
#
# Se escribe una vez con los tipos de Postgres y se traduce a SQLite, en
# vez de mantener dos copias que se desincronizan a la primera columna
# nueva.
TABLAS = {
    "operaciones": """
        id            {serial},
        ts            TEXT NOT NULL,
        fecha         TEXT NOT NULL,
        simbolo       TEXT NOT NULL,
        nombre        TEXT,
        ventana       TEXT,
        sesgo_htf     TEXT,
        setup         TEXT NOT NULL,
        zona          TEXT,
        lado          TEXT,
        entrada       DOUBLE PRECISION,
        sl            DOUBLE PRECISION,
        tp            DOUBLE PRECISION,
        rr_objetivo   DOUBLE PRECISION,
        lotes         DOUBLE PRECISION,
        riesgo_pct    DOUBLE PRECISION,
        confluencias  TEXT,
        calidad       INTEGER,
        estado        TEXT,
        r_bruto       DOUBLE PRECISION,
        costo_r       DOUBLE PRECISION,
        r             DOUBLE PRECISION,
        salida        TEXT,
        esquema       TEXT,
        expira        TEXT,
        siguio_plan   INTEGER DEFAULT 1,
        nota          TEXT,
        origen        TEXT DEFAULT 'sistema'
    """,
    "planes": """
        id            {serial},
        ts            TEXT NOT NULL,
        fecha         TEXT NOT NULL,
        simbolo       TEXT NOT NULL,
        sesgo         TEXT,
        confianza     TEXT,
        fase          TEXT,
        objetivo      TEXT,
        escenarios    TEXT,
        cumplido      INTEGER
    """,
    "corridas": """
        id            {serial},
        ts            TEXT NOT NULL,
        etiqueta      TEXT,
        n             INTEGER,
        esquema       TEXT,
        resumen       TEXT,
        esquemas      TEXT,
        modelos       TEXT,
        ventanas      TEXT,
        activos       TEXT,
        calidad       TEXT,
        confluencias  TEXT,
        embudo        TEXT,
        vetos         TEXT,
        montecarlo    TEXT,
        serie         TEXT,
        texto         TEXT
    """,
}

INDICES = [
    ("idx_fecha", "operaciones", "fecha"),
    ("idx_setup", "operaciones", "setup"),
    ("idx_origen", "operaciones", "origen"),
    ("idx_estado", "operaciones", "estado"),
    ("idx_plan_fecha", "planes", "fecha"),
]

_creado = False


def _crear(cur, pg: bool) -> None:
    serial = ("INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY" if pg
              else "INTEGER PRIMARY KEY AUTOINCREMENT")
    pre = f"{ESQUEMA_PG}." if pg else ""
    if pg:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {ESQUEMA_PG}")
    for nombre, cols in TABLAS.items():
        cur.execute(f"CREATE TABLE IF NOT EXISTS {pre}{nombre} "
                    f"({cols.format(serial=serial)})")
    for idx, tabla, col in INDICES:
        cur.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON {pre}{tabla} ({col})")


def _conexion():
    """
    Una conexion por hilo, reutilizada.

    Por hilo y no global porque el backtest baja datos con
    `asyncio.to_thread` y ni sqlite3 ni psycopg garantizan que una
    conexion se pueda compartir entre hilos.
    """
    global _creado
    c = getattr(_local, "con", None)
    if c is not None:
        try:
            if usa_postgres() and c.closed:
                c = None
        except Exception:  # noqa: BLE001
            c = None
    if c is None:
        if usa_postgres():
            import psycopg
            c = psycopg.connect(os.getenv("DATABASE_URL"), autocommit=True)
        else:
            RUTA_SQLITE.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(RUTA_SQLITE, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.isolation_level = None
        _local.con = c
        _creado = False
    if not _creado:
        with _cursor(c) as cur:
            _crear(cur, usa_postgres())
        if not usa_postgres():
            c.commit()
        _creado = True
    return c


def _cursor(c):
    """
    Un cursor cerrable en los dos motores.

    `psycopg` devuelve un cursor que es gestor de contexto; el de
    `sqlite3` no lo es hasta 3.13. `closing` los iguala sin tener que
    preguntar cual es cual en cada consulta.
    """
    return contextlib.closing(c.cursor())


def tabla(nombre: str) -> str:
    """El nombre cualificado, para pegarlo en una consulta."""
    return f"{ESQUEMA_PG}.{nombre}" if usa_postgres() else nombre


def _traducir(sql: str) -> str:
    """`?` es el marcador de sqlite; psycopg usa `%s`."""
    return sql.replace("?", "%s") if usa_postgres() else sql


def _tirar_conexion() -> None:
    """Olvida la conexion de este hilo para que la siguiente sea nueva."""
    c = getattr(_local, "con", None)
    _local.con = None
    if c is not None:
        with contextlib.suppress(Exception):
            c.close()


def reconectando(fn):
    """
    Reintenta una vez cuando la conexion se murio entre dos consultas.

    POR QUE HACE FALTA
    La conexion a Postgres se guarda por hilo y se reutiliza, que es lo
    correcto: abrir una por consulta contra Neon cuesta mas que la
    consulta. Pero una conexion guardada se muere sola por dos motivos
    que aqui pasan todos los dias:

      - el portatil se suspende. El piloto vuelve en si una hora y media
        despues y el socket lleva rato cerrado del otro lado
      - Neon apaga el compute cuando no lo usan, y al apagarlo corta las
        conexiones abiertas

    En los dos casos la primera consulta al volver revienta con
    `OperationalError: server closed the connection unexpectedly`. El
    bucle del piloto lo cazaba y decia «se reintenta en el siguiente»,
    pero el siguiente usaba la misma conexion muerta: el ciclo quedaba
    fallando en bucle sin cerrar posiciones ni abrir nuevas, con dinero
    expuesto en el broker.

    Se reintenta UNA vez y solo despues de tirar la conexion, para que
    `_conexion` abra otra. Si el segundo intento tambien falla, el error
    es de verdad y sube: no interesa disimular una base caida.
    """
    @functools.wraps(fn)
    def envoltura(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as exc:  # noqa: BLE001
            if not _es_conexion_rota(exc):
                raise
            log.warning("la conexion se habia caido (%s); reconectando",
                        type(exc).__name__)
            _tirar_conexion()
            return fn(*a, **k)
    return envoltura


def _es_conexion_rota(exc: Exception) -> bool:
    """
    ¿Es «se cayo el cable» o es «tu SQL esta mal»?

    Se mira el nombre de la clase y no la clase misma para no importar
    psycopg aqui: este modulo funciona con sqlite a secas y la
    dependencia es opcional. `OperationalError` e `InterfaceError` son
    los dos que la DB-API reserva para problemas de transporte.
    """
    if type(exc).__name__ not in ("OperationalError", "InterfaceError"):
        return False
    m = str(exc).lower()
    return any(s in m for s in (
        "server closed the connection",
        "connection is closed",
        "connection already closed",
        "consuming input failed",
        "ssl connection has been closed",
        "terminating connection",
        "bad connection",
        "could not connect",
        "eof detected",
    ))


@reconectando
def consultar(sql: str, args: list | tuple = ()) -> list[dict]:
    """Devuelve filas como diccionarios, en los dos motores."""
    c = _conexion()
    with _cursor(c) as cur:
        cur.execute(_traducir(sql), list(args))
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, f, strict=False)) for f in cur.fetchall()]


@reconectando
def ejecutar(sql: str, args: list | tuple = ()) -> int:
    """Para UPDATE y DELETE. Devuelve cuantas filas cambiaron."""
    c = _conexion()
    with _cursor(c) as cur:
        cur.execute(_traducir(sql), list(args))
        n = cur.rowcount
    if not usa_postgres():
        c.commit()
    return n


@reconectando
def insertar(nombre: str, campos: tuple[str, ...], valores: list) -> int:
    """
    Inserta una fila y devuelve su id.

    `lastrowid` no existe en psycopg, asi que en Postgres se pide el id
    de vuelta con RETURNING. Los dos caminos devuelven lo mismo para que
    quien llama no tenga que saber cual se uso.
    """
    c = _conexion()
    cols = ",".join(campos)
    pg = usa_postgres()
    marcas = ",".join(["%s" if pg else "?"] * len(campos))
    sql = f"INSERT INTO {tabla(nombre)} ({cols}) VALUES ({marcas})"
    with _cursor(c) as cur:
        if pg:
            cur.execute(sql + " RETURNING id", valores)
            return cur.fetchone()[0]
        cur.execute(sql, valores)
        rid = cur.lastrowid
    c.commit()
    return rid
