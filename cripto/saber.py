"""
Lo que el agente cree saber, versionado y en la base.

POR QUE NO EN ARCHIVOS
──────────────────────
Las notas nacieron como `.md` en el repositorio, escritas a mano despues
de mirar los numeros. Eso tiene dos problemas para un agente que aprende
solo: el disco de Render se borra en cada reinicio, y un archivo solo
guarda la ultima version -- se pierde COMO fue cambiando de opinion, que
es justamente lo que permite auditar si aprende o si deriva.

Aca cada reescritura es una fila nueva. La vigente es la de version mas
alta; las anteriores quedan, con el motivo y con cuantas operaciones
habia cuando se escribio.

LOS ARCHIVOS SIGUEN SIENDO LA SEMILLA
La primera vez la base esta vacia, asi que se siembra desde
`cripto/notas/`. Despues de eso manda la base. Los `.md` quedan como
punto de partida reproducible: quien clone el repositorio arranca con el
mismo conocimiento inicial y no con lo que aprendio la instalacion de
otro.
"""

from __future__ import annotations

import logging
import pathlib

log = logging.getLogger("cripto.saber")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS cripto.notas (
    id        BIGSERIAL PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
    nombre    TEXT NOT NULL,
    cuerpo    TEXT NOT NULL,
    version   INT  NOT NULL DEFAULT 1,
    -- Cuantas operaciones resueltas habia cuando se escribio. Sin esto no
    -- se puede saber si una nota se apoya en 30 datos o en 3000.
    apoyo     INT,
    motivo    TEXT,
    UNIQUE (nombre, version)
);
CREATE INDEX IF NOT EXISTS idx_notas_vig ON cripto.notas (nombre, version DESC);
"""

_AQUI = pathlib.Path(__file__).parent
SEMILLAS = [_AQUI / "notas", _AQUI.parent / "vault" / "cripto"]


def _semilla() -> dict[str, str]:
    """Las notas del repositorio, para arrancar."""
    carpeta = next((c for c in SEMILLAS if c.is_dir()), None)
    if carpeta is None:
        return {}
    try:
        return {f.stem: f.read_text(encoding="utf-8").strip()
                for f in sorted(carpeta.glob("*.md"))}
    except OSError:
        return {}


async def preparar(pool) -> None:
    """Crea la tabla y la siembra si esta vacia."""
    async with pool.acquire() as c:
        await c.execute(ESQUEMA)
        if await c.fetchval("SELECT count(*) FROM cripto.notas"):
            return
        sem = _semilla()
        if not sem:
            log.warning("no hay notas para sembrar")
            return
        await c.executemany(
            "INSERT INTO cripto.notas (nombre, cuerpo, version, motivo) "
            "VALUES ($1, $2, 1, 'semilla del repositorio')",
            [(n, cuerpo) for n, cuerpo in sem.items()])
        log.info("sembradas %d notas desde el repositorio", len(sem))


async def vigentes(pool) -> dict[str, str]:
    """La version mas alta de cada nota."""
    filas = await pool.fetch("""
        SELECT DISTINCT ON (nombre) nombre, cuerpo
        FROM cripto.notas ORDER BY nombre, version DESC""")
    return {f["nombre"]: f["cuerpo"] for f in filas}


async def guardar(pool, nombre: str, cuerpo: str, apoyo: int,
                  motivo: str) -> int:
    """
    Una version nueva. Nunca pisa la anterior.

    Devuelve el numero de version. Si el cuerpo es identico al vigente no
    escribe nada y devuelve 0: una reescritura que no cambia nada
    ensuciaria el historial y haria parecer que aprendio cuando no.
    """
    act = await pool.fetchrow(
        "SELECT cuerpo, version FROM cripto.notas WHERE nombre = $1 "
        "ORDER BY version DESC LIMIT 1", nombre)
    if act and act["cuerpo"].strip() == cuerpo.strip():
        return 0
    v = (act["version"] + 1) if act else 1
    await pool.execute(
        "INSERT INTO cripto.notas (nombre, cuerpo, version, apoyo, motivo) "
        "VALUES ($1, $2, $3, $4, $5)", nombre, cuerpo, v, apoyo, motivo)
    return v


OPERATIVAS = [
    "la-tabla-condicional",     # la consulta que decide el lado
    "elegir-lado",              # el procedimiento
    "la-matematica-del-peaje",  # el umbral de rentabilidad
    "cuanto-se-va-a-mover",     # el tamano esperado
    "ocho-apuestas-son-una",    # el riesgo de bloque
    "quien-decide-que",         # su rol frente a la regresion
]
"""
Que notas entran primero cuando no cabe todo.

Estas seis son las que se usan PARA DECIDIR AHORA. Las demas -- por que
fracasan las estrategias, cuanto se puede confiar, la autopsia de la
primera noche -- son metodologia: importan cuando el agente reescribe lo
que cree saber, no cuando tiene que elegir un lado en este ciclo.

El orden es el de arriba; lo que no este en la lista va despues,
alfabetico. Una nota nueva que invente el agente cae en ese resto, que
es lo correcto: todavia no se gano el lugar.
"""


async def texto(pool, tope: int = 0) -> str:
    """
    El conocimiento vigente, listo para inyectar.

    `tope` es un limite en CARACTERES, y existe por una razon concreta y
    medida: el tier gratuito de Groq admite 8.000 tokens por minuto, y
    las notas solas ya van por 10.145. Sin presupuesto, la llamada
    devuelve 413 y el ciclo no predice nada.

    Se recorta por notas enteras, nunca a mitad de una: media nota es
    peor que ninguna porque deja una afirmacion sin su numero.
    """
    n = await vigentes(pool)
    if not n:
        return ""
    orden = sorted(n, key=lambda k: (OPERATIVAS.index(k)
                                     if k in OPERATIVAS else len(OPERATIVAS), k))
    cab = "LO QUE SE SABE DE ESTE MERCADO" + chr(10) + chr(10)
    trozos, usado, fuera = [], len(cab), []
    for k in orden:
        pieza = f"### {k}" + chr(10) + n[k]
        if tope and usado + len(pieza) + 2 > tope:
            fuera.append(k)
            continue
        trozos.append(pieza)
        usado += len(pieza) + 2
    if not trozos:
        return ""
    salida = cab + (chr(10) + chr(10)).join(trozos)
    if fuera:
        # Decirlo explicitamente: el modelo tiene que saber que no vio
        # todo, para no afirmar que algo "no esta medido" cuando si lo esta.
        salida += (chr(10) + chr(10) + "(no entraron por espacio, existen y "
                   "estan medidas: " + ", ".join(fuera) + ")")
    return salida


async def historial(pool, nombre: str) -> list[dict]:
    """Como fue cambiando de opinion sobre un tema."""
    return [dict(f) for f in await pool.fetch(
        "SELECT version, ts, apoyo, motivo, length(cuerpo) largo "
        "FROM cripto.notas WHERE nombre = $1 ORDER BY version", nombre)]
