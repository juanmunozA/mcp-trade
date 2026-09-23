"""
El diario de trading. Capitulo 19.1.

POR QUE ESTO EXISTE
───────────────────
«Registrar cada operacion es la HERRAMIENTA DE MEJORA MAS PODEROSA.»
Y en la lista de errores del capitulo 20.4, el numero 8: «No llevar
diario ni metricas: NO SABER QUE FUNCIONA».

Los campos minimos que la guia exige, literales: «fecha y hora, activo,
sesion/killzone, sesgo HTF, setup (nombre de la estrategia), zona usada,
entrada/SL/TP, tamano, resultado en R, captura de pantalla antes y
despues, si se siguio el plan (si/no) y emocion dominante».

Los dos ultimos -- captura y emocion -- son de un operador humano. Un
sistema automatico siempre sigue el plan y no tiene emociones, asi que se
guardan igual pero con lo que si aplica: `siguio_plan` registra si hubo
intervencion manual, y en lugar de emocion se guarda el PUNTAJE DE
CONFLUENCIAS, que es lo que en un sistema ocupa el lugar de «que tan
convencido estaba». Dejarlos con el nombre de la guia permite que una
persona use el mismo diario a mano.

DONDE SE GUARDA
Lo decide `almacen`: Postgres cuando hay `DATABASE_URL`, un archivo
SQLite cuando no. En Render hace falta Postgres porque el disco es
efimero y un forward test de «1-3 meses» no puede empezar de cero en cada
despliegue.

LOS TRES ORIGENES, QUE NUNCA SE MEZCLAN
    backtest   simulado sobre historico. Se borra y se rehace cada corrida.
    papel      emitido en vivo ANTES de saber el resultado. No se borra nunca.
    sistema    senales informativas y vetos, sin resultado.

Promediarlos daria un numero sin sentido. El backtest es una hipotesis; el
papel es la prueba. Compararlos es justo lo que pide el paso 7 del
capitulo 20.1 -- «comparar resultados reales vs backtest» -- y para
compararlos hay que tenerlos separados.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta

from materias import almacen as A
from materias import metricas as M

RUTA = A.RUTA_SQLITE

CAMPOS = ("ts", "fecha", "simbolo", "nombre", "ventana", "sesgo_htf",
          "setup", "zona", "lado", "entrada", "sl", "tp", "rr_objetivo",
          "lotes", "riesgo_pct", "confluencias", "calidad", "estado",
          "r_bruto", "costo_r", "r", "salida", "esquema", "expira",
          "siguio_plan", "nota", "origen")

CAMPOS_PLAN = ("ts", "fecha", "simbolo", "sesgo", "confianza", "fase",
               "objetivo", "escenarios", "cumplido")

CAMPOS_CORRIDA = ("ts", "etiqueta", "n", "esquema", "resumen", "esquemas",
                  "modelos", "ventanas", "activos", "calidad", "confluencias",
                  "embudo", "vetos", "montecarlo", "serie", "texto")

JSON_OP = ("zona", "confluencias")
JSON_CORRIDA = ("resumen", "esquemas", "modelos", "ventanas", "activos",
                "calidad", "confluencias", "embudo", "vetos", "montecarlo",
                "serie")


def _texto(d: dict, claves) -> dict:
    """Serializa a JSON los campos que lo son, dejando el resto intacto."""
    out = dict(d)
    for k in claves:
        if isinstance(out.get(k), (list, dict)):
            out[k] = json.dumps(out[k], ensure_ascii=False, default=str)
    return out


def _leer_json(d: dict, claves) -> dict:
    for k in claves:
        if isinstance(d.get(k), str) and d[k]:
            try:
                d[k] = json.loads(d[k])
            except (ValueError, TypeError):
                pass
    return d


# ══════════════════════════════════════════════════════════════════════
# Operaciones
# ══════════════════════════════════════════════════════════════════════

def guardar(op: dict) -> int:
    """
    Registra una operacion con los campos del capitulo 19.1.

    Todo campo que falte queda nulo en vez de inventado: un diario con
    huecos honestos sirve; uno con valores por defecto plausibles miente
    en la revision.
    """
    d = _texto(op, JSON_OP)
    d.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    d.setdefault("fecha", str(d["ts"])[:10])
    return A.insertar("operaciones", CAMPOS, [d.get(c) for c in CAMPOS])


def guardar_muchas(ops: list[dict]) -> int:
    """Carga en bloque, para volcar un backtest entero al diario."""
    return sum(1 for o in ops if guardar(o))


def actualizar(id_: int, campos: dict) -> None:
    """
    Cambia el estado y el resultado de una operacion ya registrada.

    Deliberadamente NO puede tocar entrada, SL, TP ni ts. Una operacion en
    papel vale justo por haberse escrito ANTES de saber el resultado; si
    el codigo pudiera reescribir sus niveles despues, el forward test
    seria un backtest disfrazado y no habria forma de notarlo mirando la
    base.
    """
    permitidos = {"estado", "r", "r_bruto", "salida", "esquema", "nota",
                  "siguio_plan", "costo_r"}
    campos = {k: v for k, v in campos.items() if k in permitidos}
    if not campos:
        return
    sets = ", ".join(f"{k} = ?" for k in campos)
    A.ejecutar(f"UPDATE {A.tabla('operaciones')} SET {sets} WHERE id = ?",
               [*campos.values(), id_])


def leer(desde: str | None = None, hasta: str | None = None,
         setup: str | None = None, origen: str | None = None) -> list[dict]:
    """Operaciones ya cerradas, con resultado."""
    cond, args = ["r IS NOT NULL"], []
    for campo, val, op in (("fecha", desde, ">="), ("fecha", hasta, "<="),
                           ("setup", setup, "="), ("origen", origen, "=")):
        if val:
            cond.append(f"{campo} {op} ?")
            args.append(val)
    filas = A.consultar(
        f"SELECT * FROM {A.tabla('operaciones')} WHERE {' AND '.join(cond)} "
        f"ORDER BY ts", args)
    return [_leer_json(f, JSON_OP) for f in filas]


def leer_estado(estados: tuple[str, ...] | None = None,
                origen: str = "") -> list[dict]:
    """Operaciones por estado y origen, cerradas o no."""
    cond, args = ["1=1"], []
    if estados:
        cond.append(f"estado IN ({','.join('?' * len(estados))})")
        args.extend(estados)
    if origen:
        cond.append("origen = ?")
        args.append(origen)
    filas = A.consultar(
        f"SELECT * FROM {A.tabla('operaciones')} WHERE {' AND '.join(cond)} "
        f"ORDER BY id", args)
    return [_leer_json(f, JSON_OP) for f in filas]


def listar(n: int = 200, origen: str = "", setup: str = "",
           simbolo: str = "", ventana: str = "") -> list[dict]:
    """Las mas recientes, con filtros. Para el tablero."""
    cond, args = ["1=1"], []
    for campo, val in (("origen", origen), ("setup", setup),
                       ("simbolo", simbolo), ("ventana", ventana)):
        if val:
            cond.append(f"{campo} = ?")
            args.append(val)
    filas = A.consultar(
        f"SELECT * FROM {A.tabla('operaciones')} WHERE {' AND '.join(cond)} "
        f"ORDER BY id DESC LIMIT {max(1, min(n, 600))}", args)
    return [_leer_json(f, JSON_OP) for f in filas]


def borrar_backtest() -> int:
    """
    Vacia las operaciones de backtest. Nunca las de papel.

    Cada corrida vuelve a generar las mismas operaciones sobre los mismos
    datos; acumularlas duplicaria la muestra y con ella todas las
    metricas. Las de papel se emitieron en vivo y no se pueden rehacer:
    esas no las toca nadie desde aqui.
    """
    return A.ejecutar(
        f"DELETE FROM {A.tabla('operaciones')} WHERE origen = 'backtest'")


# ══════════════════════════════════════════════════════════════════════
# Planes del dia
# ══════════════════════════════════════════════════════════════════════

def guardar_plan(p: dict) -> int:
    """El plan del dia (cap. 3.2, paso 5), para comparar despues."""
    d = {"ts": datetime.now().isoformat(timespec="seconds"),
         "fecha": str(p.get("hora", ""))[:10] or str(date.today()),
         "simbolo": p.get("simbolo"), "sesgo": p.get("sesgo"),
         "confianza": p.get("confianza"), "fase": p.get("fase"),
         "objetivo": p.get("objetivo_liquidez"),
         "escenarios": json.dumps(p.get("escenarios", []), ensure_ascii=False),
         "cumplido": None}
    return A.insertar("planes", CAMPOS_PLAN, [d[k] for k in CAMPOS_PLAN])


def planes(fecha: str = "") -> list[dict]:
    """El plan mas reciente por activo, o todos los de un dia."""
    if fecha:
        filas = A.consultar(
            f"SELECT * FROM {A.tabla('planes')} WHERE fecha = ? ORDER BY id DESC",
            [fecha])
    else:
        filas = A.consultar(
            f"SELECT * FROM {A.tabla('planes')} WHERE id IN "
            f"(SELECT max(id) FROM {A.tabla('planes')} GROUP BY simbolo) "
            f"ORDER BY simbolo")
    return [_leer_json(f, ("escenarios",)) for f in filas]


# ══════════════════════════════════════════════════════════════════════
# Corridas de backtest
# ══════════════════════════════════════════════════════════════════════

def guardar_corrida(d: dict) -> int:
    """
    Persiste una corrida entera: embudo, esquemas, Monte Carlo y todo.

    Nada de eso es propiedad de una operacion, asi que no cabe en la tabla
    de operaciones. Guardarlo permite que el tablero lo muestre sin
    repetir cuatro minutos de simulacion, y que dos corridas se comparen
    -- que es lo que exige el paso 8 del capitulo 20.1.
    """
    x = _texto(d, JSON_CORRIDA)
    x.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    return A.insertar("corridas", CAMPOS_CORRIDA,
                      [x.get(k) for k in CAMPOS_CORRIDA])


def ultima_corrida() -> dict:
    filas = A.consultar(
        f"SELECT * FROM {A.tabla('corridas')} ORDER BY id DESC LIMIT 1")
    return _leer_json(filas[0], JSON_CORRIDA) if filas else {}


def corridas(n: int = 20) -> list[dict]:
    """El historial, para comparar antes y despues de cada ajuste."""
    filas = A.consultar(
        f"SELECT id, ts, etiqueta, n, esquema, resumen "
        f"FROM {A.tabla('corridas')} ORDER BY id DESC LIMIT {max(1, min(n, 100))}")
    return [_leer_json(f, ("resumen",)) for f in filas]


def informe() -> str:
    filas = A.consultar(
        f"SELECT texto FROM {A.tabla('corridas')} ORDER BY id DESC LIMIT 1")
    return (filas[0]["texto"] if filas and filas[0]["texto"]
            else "sin corridas guardadas")


# ══════════════════════════════════════════════════════════════════════
# Lecturas
# ══════════════════════════════════════════════════════════════════════

def estado() -> dict:
    """
    Que hay en el diario, para el log de arranque y para /salud.

    Se separan las CERRADAS de las que siguen vivas: una senal con la
    orden todavia pendiente no tiene resultado, y contarla junto a las
    cerradas daria una muestra inflada -- justo el numero sobre el que el
    capitulo 18.7 pone su liston de 100-300.
    """
    r = A.consultar(
        f"SELECT count(*) n, min(fecha) d0, max(fecha) d1, sum(r) total "
        f"FROM {A.tabla('operaciones')} WHERE r IS NOT NULL")[0]
    otras = A.consultar(
        f"SELECT estado, count(*) n FROM {A.tabla('operaciones')} "
        f"WHERE r IS NULL GROUP BY estado")
    return {"n": r["n"] or 0, "desde": r["d0"], "hasta": r["d1"],
            "r_total": round(float(r["total"] or 0), 2),
            "sin_cerrar": {x["estado"] or "?": x["n"] for x in otras},
            "donde": A.destino()}


def _agrupar(ops: list[dict], clave: str) -> dict[str, list[float]]:
    g: dict[str, list[float]] = defaultdict(list)
    for o in ops:
        g[str(o.get(clave) or "?")].append(o["r"])
    return g


def revision(dias: int = 7, riesgo_pct: float = 0.5,
             origen: str = "papel") -> str:
    """
    La revision semanal del capitulo 19.1.

    «Que setups funcionan, en que horario, que errores se repiten.» Las
    tres preguntas, en ese orden, con la muestra de cada una al lado para
    que se vea cuando una conclusion no tiene con que sostenerse.

    Por defecto mira el PAPEL y no el backtest: el backtest ya se revisa
    solo cada vez que corre, y lo que hay que vigilar semana a semana es
    si lo que se emitio en vivo se parece a lo que prometia.
    """
    hasta = date.today()
    desde = hasta - timedelta(days=dias)
    ops = leer(str(desde), str(hasta), origen=origen)
    L = [f"REVISION · {origen} · {desde} a {hasta}  (cap. 19.1)", ""]
    if not ops:
        L.append(f"  sin operaciones de «{origen}» cerradas en el periodo")
        return chr(10).join(L)

    L.extend("  " + x for x in
             M.informe(M.calcular([o["r"] for o in ops], riesgo_pct), riesgo_pct))

    def _por(clave: str, titulo: str) -> None:
        L.append("")
        L.append(f"  {titulo}")
        for k, lst in sorted(_agrupar(ops, clave).items(),
                             key=lambda x: -sum(x[1])):
            m = M.calcular(lst, riesgo_pct)
            corta = "   (muestra corta)" if m.n < 20 else ""
            L.append(f"    {k:26} n={m.n:3}  wr {m.win_rate:5.1f}%  "
                     f"{m.r_total:+7.2f}R  exp {m.expectativa:+.3f}R{corta}")

    _por("setup", "¿QUE SETUPS FUNCIONAN?")
    _por("ventana", "¿EN QUE HORARIO?")
    _por("simbolo", "¿EN QUE ACTIVO?")

    fuera = [o for o in ops if not o.get("siguio_plan", 1)]
    L.append("")
    L.append("  ¿QUE ERRORES SE REPITEN?")
    if fuera:
        m_si = M.calcular([o["r"] for o in ops if o.get("siguio_plan", 1)], riesgo_pct)
        m_no = M.calcular([o["r"] for o in fuera], riesgo_pct)
        L.append(f"    {len(fuera)} de {len(ops)} operaciones NO siguieron el plan")
        L.append(f"    siguiendo el plan:  exp {m_si.expectativa:+.3f}R  (n={m_si.n})")
        L.append(f"    saltandoselo:       exp {m_no.expectativa:+.3f}R  (n={m_no.n})")
    else:
        L.append("    todas las operaciones siguieron el plan")

    L.append("")
    L.append("  SIGUIENTE AJUSTE  (cap. 20.1, paso 8: «UN SOLO PARAMETRO A LA VEZ»)")
    peores = sorted(((k, M.calcular(v, riesgo_pct))
                     for k, v in _agrupar(ops, "setup").items()),
                    key=lambda x: x[1].expectativa)
    if peores and peores[0][1].n >= 20 and peores[0][1].expectativa < 0:
        k, m = peores[0]
        L.append(f"    el modelo «{k}» lleva {m.n} operaciones con expectativa "
                 f"{m.expectativa:+.3f}R.")
        L.append("    Candidato a desactivar. Cambiar SOLO eso y volver a medir.")
    else:
        L.append("    ningun modelo tiene muestra suficiente (n>=20) con "
                 "expectativa negativa. No tocar nada todavia.")
    return chr(10).join(L)


def comparar_con_plan(fecha: str) -> str:
    """
    El plan escrito contra lo que paso (cap. 3.2, paso 5 + cap. 19.1).

    Escribir el escenario y no volver a mirarlo desperdicia la mitad del
    valor. Esto pone los dos al lado: que se esperaba y que se opero.
    """
    ps = planes(fecha)
    ops = leer(fecha, fecha)
    L = [f"PLAN vs REALIDAD · {fecha}", ""]
    if not ps:
        L.append("  no se guardo plan para ese dia")
    for p in ps:
        suyas = [o for o in ops if o["simbolo"] == p["simbolo"]]
        r = sum(o["r"] for o in suyas)
        L.append(f"  {p['simbolo']}  sesgo {p['sesgo']} ({p['confianza']})"
                 f"  fase {p['fase']}")
        L.append(f"    esperaba: {p['objetivo']}")
        if suyas:
            lados = {o["lado"] for o in suyas}
            coincide = ("si" if (p["sesgo"] == "alcista" and lados == {"compra"})
                        or (p["sesgo"] == "bajista" and lados == {"venta"})
                        else "no")
            L.append(f"    opero:    {len(suyas)} operacion(es) "
                     f"{'/'.join(sorted(lados))}, {r:+.2f}R  "
                     f"(en la direccion del plan: {coincide})")
        else:
            L.append("    opero:    nada. «Si no aparece, no hay operacion»")
    return chr(10).join(L)


if __name__ == "__main__":
    print(f"diario en {A.destino()}")
    print(estado())
    print()
    print(revision())
