"""
Lo que el agente aprende operando. El ciclo que cierra. Capitulo 19.1 y 20.1.

QUE ES APRENDER AQUI
────────────────────
La guia no cambia: son 56 paginas fijas y el agente las tiene enteras en
cada decision. Lo que cambia es lo que el agente descubre APLICANDOLAS a
este activo, en este horario, con este broker.

Eso es exactamente lo que la guia llama especializarse. Capitulo 20.1,
paso 1: «elige un solo activo y una o dos sesiones. La ESPECIALIZACION
permite conocer su personalidad: rango diario, horas de movimiento,
reaccion a noticias». Esa personalidad no esta en el documento -- no puede
estarlo, el documento no sabe que activo vas a elegir -- y solo aparece
midiendo.

Y el paso 8: «revisa y ajusta UN SOLO PARAMETRO A LA VEZ». Las notas son
donde queda escrito que se ajusto y por que.

COMO SE APRENDE
Cada N operaciones cerradas, el agente recibe sus propios resultados
medidos -- por modelo, por ventana, por confluencia -- y reescribe sus
notas. No se le pide que opine: se le da la tabla y se le pide que diga
que ve, con la muestra al lado de cada afirmacion.

LA REGLA QUE IMPIDE QUE SE INVENTE COSAS
Una nota necesita APOYO: cuantas operaciones la sostienen. Sin eso, a las
tres operaciones el agente escribe «el Silver Bullet funciona muy bien en
oro» y a partir de ahi decide con esa frase como si fuera un hecho. El
capitulo 18.7 pone el liston en «> 100, idealmente > 300», y aunque una
nota no necesite 300 para ser util, si necesita decir sobre cuantas se
apoya para que el propio agente sepa cuanto pesarla.

POR QUE VERSIONADAS Y NO SOBRESCRITAS
Porque si una nota empeora los resultados hay que poder ver cuando entro
y que decia antes. Sobrescribir borra justo la evidencia que haria falta
para revertirla.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime

from materias import almacen as A
from materias import diario as DI
from materias import metricas as M

log = logging.getLogger("materias.saber")

CADA = int(os.getenv("MATERIAS_APRENDER_CADA", "25"))
"""
Cada cuantas operaciones cerradas nuevas se reescriben las notas.

25 y no 5 porque con cinco operaciones no hay nada que aprender, solo
ruido que suena a patron -- y una nota escrita sobre ruido contamina
todas las decisiones siguientes. Tampoco 200: a dos o tres operaciones
por dia serian meses hasta la primera revision.
"""

TABLA = """
CREATE TABLE IF NOT EXISTS notas (
    id       {serial},
    ts       TEXT NOT NULL,
    version  INTEGER NOT NULL,
    apoyo    INTEGER,
    motivo   TEXT,
    cuerpo   TEXT NOT NULL
)
"""


def _asegurar() -> None:
    serial = ("INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY"
              if A.usa_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT")
    A.ejecutar(TABLA.format(serial=serial).replace(
        "notas", A.tabla("notas"), 1))


def leer() -> str:
    """Las notas vigentes. Es lo que va al contexto del agente."""
    _asegurar()
    f = A.consultar(
        f"SELECT cuerpo FROM {A.tabla('notas')} ORDER BY version DESC LIMIT 1")
    return f[0]["cuerpo"] if f else ""


def version() -> int:
    _asegurar()
    f = A.consultar(f"SELECT max(version) v FROM {A.tabla('notas')}")
    return (f[0]["v"] or 0) if f else 0


def historial(n: int = 10) -> list[dict]:
    _asegurar()
    return A.consultar(
        f"SELECT id, ts, version, apoyo, motivo, length(cuerpo) largo "
        f"FROM {A.tabla('notas')} ORDER BY version DESC LIMIT {max(1, min(n, 50))}")


def guardar(cuerpo: str, apoyo: int, motivo: str) -> int:
    _asegurar()
    return A.insertar("notas", ("ts", "version", "apoyo", "motivo", "cuerpo"),
                      [datetime.now().isoformat(timespec="seconds"),
                       version() + 1, apoyo, motivo, cuerpo])


# ══════════════════════════════════════════════════════════════════════
# La evidencia
# ══════════════════════════════════════════════════════════════════════

def evidencia(origen: str = "") -> str:
    """
    Los resultados medidos del propio agente, en texto.

    Se le dan NUMEROS con su muestra al lado, no conclusiones. Si el
    resumen ya dijera «el modelo 11.5 funciona», el agente estaria
    copiando mi lectura de sus datos en vez de leerlos.
    """
    ops = [o for o in DI.leer(origen=origen or None) if o.get("r") is not None]
    if not ops:
        return ""
    L = []
    m = M.calcular([o["r"] for o in ops])
    L.append(f"Operaciones cerradas: {m.n}   win rate {m.win_rate:.1f}%   "
             f"total {m.r_total:+.2f}R   expectativa {m.expectativa:+.3f}R")
    L.append(f"Ganador medio {m.ganancia_media:.2f}R, perdedor {m.perdida_media:.2f}R "
             f"-> R:R conseguido {m.rr_efectivo:.2f}, equilibrio "
             f"{m.win_rate_equilibrio:.1f}% (cap. 14.3)")
    L.append(f"Peor racha: {m.peor_racha} perdidas seguidas. "
             f"Drawdown maximo {m.max_drawdown_r:.2f}R")

    def _por(clave: str, titulo: str) -> None:
        g: dict[str, list[float]] = defaultdict(list)
        for o in ops:
            g[str(o.get(clave) or "?")].append(o["r"])
        if len(g) < 2:
            return
        L.append("")
        L.append(titulo)
        for k, lst in sorted(g.items(), key=lambda x: -sum(x[1])):
            mm = M.calcular(lst)
            L.append(f"  {k:28} n={mm.n:3}  wr {mm.win_rate:5.1f}%  "
                     f"{mm.r_total:+7.2f}R  expectativa {mm.expectativa:+.3f}R")

    _por("setup", "Por modelo de la guia:")
    _por("ventana", "Por ventana horaria:")
    _por("lado", "Por direccion:")
    _por("sesgo_htf", "Por sesgo de temporalidad alta:")

    # Lo que se descarto tambien ensena. El capitulo 19.1 lo pide: «que
    # errores se repiten».
    vetos = [o for o in DI.leer_estado(("vetada",)) if o.get("nota")]
    if vetos:
        c: dict[str, int] = defaultdict(int)
        for v in vetos:
            c[str(v["nota"])[:70]] += 1
        L.append("")
        L.append("Senales que el checklist freno:")
        for k, n in sorted(c.items(), key=lambda x: -x[1])[:8]:
            L.append(f"  {n:4}  {k}")
    return "\n".join(L)


PETICION = """Estas son TUS operaciones cerradas, medidas. Reescribe tus
notas de trabajo a partir de ellas.

QUE SON ESTAS NOTAS
Lo que has descubierto aplicando la guia a ESTE activo en ESTE horario.
La guia no cambia y no se resume aqui: la tienes entera. Las notas son la
capa de encima -- la «personalidad» del activo que el capitulo 20.1 dice
que solo se conoce especializandose.

REGLAS
1. Cada afirmacion lleva su muestra: «con n=31». Sin numero no se
   escribe. Una nota sobre tres operaciones es ruido con forma de patron.
2. Nada que contradiga la guia. Si tus datos parecen contradecirla, la
   nota es «sobre esta muestra el 11.7 dio X», no «el capitulo 11.7 esta
   equivocado». El capitulo 18.7 pide >100 operaciones para concluir.
3. Un solo cambio por revision (cap. 20.1, paso 8). Di cual es y por que.
4. Escribe lo que NO funciona con el mismo cuidado que lo que si. Saber
   que un modelo pierde es mas util que saber que otro gana.
5. Si la muestra no da para nada, dilo y deja las notas como estaban. Es
   una respuesta valida y es la correcta casi siempre al principio.

Devuelve solo las notas, en markdown, sin preambulo."""


def aprender(origen: str = "") -> dict:
    """
    El agente relee sus resultados y reescribe sus notas.

    Devuelve que hizo. Si no hay muestra suficiente, no llama al modelo:
    gastar una llamada para que diga «no hay datos» es gastarla.
    """
    from materias import agente as AG

    ops = [o for o in DI.leer(origen=origen or None) if o.get("r") is not None]
    if len(ops) < CADA:
        return {"cambio": False,
                "motivo": f"{len(ops)} operaciones cerradas, hacen falta {CADA}"}

    v = version()
    ultimo = A.consultar(
        f"SELECT apoyo FROM {A.tabla('notas')} ORDER BY version DESC LIMIT 1")
    apoyo_previo = (ultimo[0]["apoyo"] or 0) if ultimo else 0
    if len(ops) - apoyo_previo < CADA:
        return {"cambio": False,
                "motivo": f"solo {len(ops) - apoyo_previo} operaciones nuevas "
                          f"desde la version {v}"}

    datos = evidencia(origen)
    notas = leer()
    sistema = [
        {"type": "text", "text": AG.SISTEMA},
        {"type": "text",
         "text": "# LA GUIA (integra, 56 paginas)\n\n" + __import__(
             "materias.guia", fromlist=["texto"]).texto(),
         "cache_control": {"type": "ephemeral"}},
    ]
    mensaje = PETICION
    if notas:
        mensaje += "\n\n# TUS NOTAS ACTUALES\n\n" + notas
    mensaje += "\n\n# TUS RESULTADOS MEDIDOS\n\n" + datos

    try:
        # Aqui no se pide JSON: la salida es prosa. Se reusa la cadena de
        # proveedores para que aprender tampoco dependa de uno solo.
        cuerpo = _texto_libre(sistema, mensaje)
    except AG.ErrorIA as exc:
        log.warning("no se pudieron reescribir las notas: %s", exc)
        return {"cambio": False, "motivo": f"sin proveedor: {exc}"}

    if not cuerpo.strip() or cuerpo.strip() == (notas or "").strip():
        return {"cambio": False, "motivo": "el agente no cambio nada"}

    nid = guardar(cuerpo, len(ops), f"revision sobre {len(ops)} operaciones")
    log.info("notas reescritas: version %d sobre %d operaciones", v + 1, len(ops))
    return {"cambio": True, "version": v + 1, "apoyo": len(ops), "id": nid}


def _texto_libre(sistema: list, mensaje: str) -> str:
    """
    Como `agente._preguntar` pero devolviendo prosa en vez de JSON.

    Las notas son para leerlas -- las lee el agente y las lee una
    persona en el tablero -- y meterlas en un esquema las volveria una
    lista de campos.
    """
    from materias import agente as AG
    import os as _os

    for prov in AG.PROVEEDORES:
        try:
            if prov == "anthropic":
                import anthropic
                if not _os.getenv("ANTHROPIC_API_KEY"):
                    raise AG.ErrorIA("sin ANTHROPIC_API_KEY")
                msg = anthropic.Anthropic().messages.create(
                    model=AG.MODELO, max_tokens=8000, system=sistema,
                    thinking={"type": "adaptive"},
                    output_config={"effort": AG.ESFUERZO},
                    messages=[{"role": "user", "content": mensaje}])
                if msg.stop_reason == "refusal":
                    raise AG.ErrorIA(f"declino: {msg.stop_details}")
                return next((b.text for b in msg.content if b.type == "text"), "")
            # Los demas ya devuelven texto; se les pide sin formato JSON.
            d, _ = AG.LLAMAR[prov](sistema, mensaje)
            return d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)
        except AG.ErrorIA as exc:
            log.warning("%s no pudo reescribir notas: %s", prov, exc)
        except KeyError:
            continue
    raise AG.ErrorIA("ningun proveedor disponible")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"notas en version {version()}  ({A.destino()})")
    print()
    ev = evidencia()
    print(ev or "  sin operaciones cerradas todavia")
    print()
    n = leer()
    print("NOTAS VIGENTES:")
    print(n or "  ninguna")
