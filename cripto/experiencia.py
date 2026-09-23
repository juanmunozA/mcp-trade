"""
Lo que el mercado hizo de verdad, recalculado en cada ciclo.

POR QUE ESTO REEMPLAZA A LOS NUMEROS DE LAS NOTAS
─────────────────────────────────────────────────
Las notas tenian la tabla condicional escrita a mano, medida sobre 261
operaciones. Con 917 la tabla se movio, y no poco:

    rsi<40 + 1h bajando     notas 0,75  ->  medido 0,66
    rsi 60-75 + 1h subiendo notas 0,35  ->  medido 0,51   (sin ventaja)
    rsi>75                  notas 0,45  ->  medido 0,60   (al reves)

El agente seguia apostando con conviccion sobre numeros vencidos. Un
numero escrito a mano envejece; uno calculado no.

Asi que las notas se quedan con lo cualitativo -- por que fracasan las
estrategias, que significa el peaje, que ocho pares correlacionados son
una apuesta -- y TODA cifra sale de aca, recalculada contra la base cada
vez que se predice.

LO RECIENTE PESA MAS
Se calcula dos veces: sobre todo el historial y sobre las ultimas horas.
Cuando las dos difieren, eso ES la senal de que el regimen cambio, y el
modelo tiene que verlo para no seguir operando un mercado que ya no
existe. Paso de verdad: el RSI acerto 67,9% en la primera mitad de los
datos y 46,0% en la segunda.

NADA SE AFIRMA SIN SU INTERVALO
Cada celda lleva cuantos casos la respaldan y su intervalo de Wilson,
ensanchado por el agrupamiento de ciclos. Una celda de 0,66 con n=196
y otra de 0,60 con n=5 no son la misma clase de cosa, y el modelo tiene
que poder distinguirlas.
"""

from __future__ import annotations

import collections
import math

NL = chr(10)

VENTANA_RECIENTE = "6 hours"
"""
Que se considera "ahora" para detectar cambios de regimen.

Seis horas son unas 70 tandas: suficiente para que un numero signifique
algo y poco para que un cambio de comportamiento todavia se note. Con
una ventana mas larga el promedio viejo tapa lo nuevo, que es justo lo
que queremos evitar.
"""


def _wilson(k: int, n: int, z: float) -> tuple[float, float]:
    if not n:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


async def _deff(pool) -> float:
    """
    Cuanto ensanchar los intervalos por el agrupamiento de ciclos.

    Ocho pares predichos en el mismo instante se mueven juntos, asi que
    las filas no son independientes. Sin esta correccion toda celda
    parece mas precisa de lo que es.
    """
    filas = await pool.fetch(
        "SELECT count(*) n, count(*) FILTER (WHERE subio) k "
        "FROM cripto.predicciones WHERE subio IS NOT NULL "
        "GROUP BY date_trunc('second', ts) HAVING count(*) > 1")
    if not filas:
        return 1.0
    tot = sum(f["n"] for f in filas)
    exitos = sum(f["k"] for f in filas)
    ob = exitos / tot if tot else 0.5
    if ob in (0.0, 1.0):
        return 1.0
    num = den = 0.0
    for f in filas:
        n, k = f["n"], f["k"]
        # suma de productos cruzados dentro del grupo
        num += k * (k - 1) + (n - k) * (n - k - 1) - 2 * k * (n - k) * 0
        den += n * (n - 1)
    if not den:
        return 1.0
    concordancia = num / den
    esperado = ob * ob + (1 - ob) * (1 - ob)
    rho = max(0.0, (concordancia - esperado) / max(1e-9, 1 - esperado))
    m = tot / len(filas)
    return max(1.0, 1 + (m - 1) * rho)


async def tabla(pool, z: float) -> list[dict]:
    """La tabla condicional rsi_5m x cambio_1h, medida contra la base."""
    filas = await pool.fetch("""
        SELECT CASE
                 WHEN (contexto->>'rsi_5m')::float < 40 THEN 'rsi<40'
                 WHEN (contexto->>'rsi_5m')::float < 50 THEN 'rsi 40-50'
                 WHEN (contexto->>'rsi_5m')::float < 60 THEN 'rsi 50-60'
                 WHEN (contexto->>'rsi_5m')::float < 75 THEN 'rsi 60-75'
                 ELSE 'rsi>75' END AS banda,
               CASE WHEN (contexto->>'cambio_1h')::float < 0
                    THEN '1h bajando' ELSE '1h subiendo' END AS hora,
               count(*) n, count(*) FILTER (WHERE subio) k
        FROM cripto.predicciones
        WHERE subio IS NOT NULL AND contexto ? 'rsi_5m'
        GROUP BY 1, 2 HAVING count(*) >= 12 ORDER BY 1, 2""")
    out = []
    for f in filas:
        lo, hi = _wilson(f["k"], f["n"], z)
        out.append({"banda": f["banda"], "hora": f["hora"], "n": f["n"],
                    "p": round(f["k"] / f["n"], 3),
                    "ic": [round(lo, 2), round(hi, 2)],
                    # Una celda cuyo intervalo cruza 0,50 no distingue nada,
                    # por mas lejos de 0,50 que caiga su punto central.
                    "sirve": lo > 0.5 or hi < 0.5})
    return out


async def variables(pool) -> list[dict]:
    """
    Cuanto separa cada variable, hoy.

    Es la prueba mas simple que existe: cuando la variable es alta, ¿sube
    mas seguido que cuando es baja? La diferencia en puntos porcentuales
    es lo que esa variable aporta. Cerca de cero significa que no sirve,
    aunque la teoria diga lo contrario.
    """
    campos = ["rsi_5m", "rsi_15m", "cambio_5m", "cambio_1h", "cambio_4h",
              "flujo_agresor", "flujo_reciente", "libro_desequilibrio",
              "volumen_vs_medio", "volatilidad_pct", "pos_en_rango_24h",
              "ema9_vs_ema21", "precio_vs_ema50"]
    out = []
    for c in campos:
        # La mediana va en su propia subconsulta: percentile_cont es un
        # agregado de conjunto ordenado y no admite OVER().
        r = await pool.fetchrow(f"""
            WITH d AS (
              SELECT (contexto->>'{c}')::float v, subio
              FROM cripto.predicciones
              WHERE subio IS NOT NULL AND contexto ? '{c}'
                AND (contexto->>'{c}') ~ '^-?[0-9.]+$'),
            m AS (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY v) med FROM d)
            SELECT count(*) FILTER (WHERE v >  (SELECT med FROM m)) n_alto,
                   count(*) FILTER (WHERE v >  (SELECT med FROM m) AND subio) k_alto,
                   count(*) FILTER (WHERE v <= (SELECT med FROM m)) n_bajo,
                   count(*) FILTER (WHERE v <= (SELECT med FROM m) AND subio) k_bajo
            FROM d""")
        if not r or not r["n_alto"] or not r["n_bajo"]:
            continue
        alto = r["k_alto"] / r["n_alto"]
        bajo = r["k_bajo"] / r["n_bajo"]
        out.append({"variable": c, "separa_pp": round(100 * (alto - bajo), 1),
                    "alto": round(alto, 3), "bajo": round(bajo, 3)})
    out.sort(key=lambda x: -abs(x["separa_pp"]))
    return out


async def regimen(pool) -> dict:
    """
    Lo reciente contra el historial completo.

    Si difieren, el mercado cambio de comportamiento y lo aprendido antes
    ya no aplica. Es la diferencia entre aprender y quedarse con lo
    aprendido.
    """
    async def _mide(cond: str) -> dict:
        r = await pool.fetchrow(f"""
            SELECT count(*) n,
                   count(*) FILTER (WHERE (p_agente > 0.5) = subio) ok,
                   round((avg(abs(precio_fin / nullif(precio_ini,0) - 1)) * 100)::numeric, 3) mov,
                   count(*) FILTER (WHERE subio) subieron
            FROM cripto.predicciones
            WHERE subio IS NOT NULL AND precio_ini > 0 AND {cond}""")
        if not r or not r["n"]:
            return {}
        return {"n": r["n"], "acierto_pct": round(100 * r["ok"] / r["n"], 1),
                "mov_medio_pct": float(r["mov"] or 0),
                "subieron_pct": round(100 * r["subieron"] / r["n"], 1)}
    return {"historico": await _mide("true"),
            "reciente": await _mide(f"ts > now() - interval '{VENTANA_RECIENTE}'")}


async def por_par(pool) -> list[dict]:
    """Cada moneda por separado: no todas se comportan igual."""
    filas = await pool.fetch("""
        SELECT simbolo, count(*) n,
               count(*) FILTER (WHERE (p_agente > 0.5) = subio) ok,
               round((avg(abs(precio_fin / nullif(precio_ini,0) - 1)) * 100)::numeric, 3) mov,
               round((sum(pnl_neto) * 100)::numeric, 2) neto
        FROM cripto.predicciones
        WHERE subio IS NOT NULL AND precio_ini > 0
        GROUP BY 1 HAVING count(*) >= 15 ORDER BY sum(pnl_neto) DESC""")
    return [{"simbolo": f["simbolo"], "n": f["n"],
             "acierto_pct": round(100 * f["ok"] / f["n"], 1),
             "mov_medio_pct": float(f["mov"] or 0),
             "neto_pct": float(f["neto"] or 0)} for f in filas]


async def umbral(pool) -> dict:
    """
    Cuanto hay que acertar para no perder, con el movimiento de HOY.

    p* = 0,5 + c/(2m). Cambia cuando cambia la volatilidad del mercado,
    asi que tenerlo fijo en una nota es tenerlo mal la mayor parte del
    tiempo.
    """
    r = await pool.fetchrow("""
        SELECT percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY abs(precio_fin / nullif(precio_ini,0) - 1)) * 100 med
        FROM cripto.predicciones WHERE subio IS NOT NULL AND precio_ini > 0""")
    m = float(r["med"] or 0.45)
    return {"mov_mediano_pct": round(m, 3),
            "acierto_necesario_pct": round(100 * (0.5 + 0.10 / (2 * m)), 1)}


async def resumen(pool, costo_pct: float = 0.10) -> str:
    """
    Todo lo anterior, como texto para inyectar en la llamada.

    Va antes que las notas a proposito: cuando una nota y una cifra medida
    se contradigan, gana la cifra. La nota explica por que; el numero dice
    cuanto.
    """
    n = await pool.fetchval(
        "SELECT count(*) FROM cripto.predicciones WHERE subio IS NOT NULL")
    if not n or n < 60:
        return ""
    deff = await _deff(pool)
    z = 1.96 * math.sqrt(deff)

    L = [f"TU EXPERIENCIA MEDIDA ({n} operaciones resueltas, recalculada este ciclo)",
         "Estas cifras salen de la base AHORA. Si una nota dice otra cosa, la nota",
         f"esta vencida y gana esto. Intervalos ya ensanchados {math.sqrt(deff):.2f}x",
         "por el agrupamiento de ciclos."]

    u = await umbral(pool)
    L.append("")
    L.append(f"EL LISTON: el movimiento mediano es {u['mov_mediano_pct']}% y la comision "
             f"{costo_pct}%,")
    L.append(f"asi que hace falta acertar el {u['acierto_necesario_pct']}% para no perder.")

    reg = await regimen(pool)
    h, rc = reg.get("historico", {}), reg.get("reciente", {})
    if h and rc:
        L.append("")
        L.append("REGIMEN -- lo reciente contra todo el historial:")
        L.append(f"  historico (n={h['n']}): acierto {h['acierto_pct']}%  "
                 f"movimiento medio {h['mov_medio_pct']}%  subio {h['subieron_pct']}%")
        L.append(f"  ultimas {VENTANA_RECIENTE} (n={rc['n']}): acierto {rc['acierto_pct']}%  "
                 f"movimiento medio {rc['mov_medio_pct']}%  subio {rc['subieron_pct']}%")
        if abs(h["acierto_pct"] - rc["acierto_pct"]) > 8:
            L.append("  -> DIFIEREN MUCHO: el mercado cambio de comportamiento. Lo que")
            L.append("     funcionaba antes puede no funcionar ahora. Baja la conviccion.")

    t = await tabla(pool, z)
    if t:
        L.append("")
        L.append("TABLA CONDICIONAL rsi_5m x cambio_1h -- P(sube) medida:")
        for c in t:
            marca = "" if c["sirve"] else "   <- el intervalo cruza 0,50: NO distingue nada"
            L.append(f"  {c['banda']:10} {c['hora']:12} {c['p']:.2f} "
                     f"IC[{c['ic'][0]:.2f},{c['ic'][1]:.2f}] n={c['n']:4}{marca}")
        utiles = [c for c in t if c["sirve"]]
        L.append(f"  Celdas que de verdad separan: {len(utiles)} de {len(t)}."
                 + (" Ninguna." if not utiles else ""))

    v = await variables(pool)
    if v:
        L.append("")
        L.append("QUE SEPARA HOY (diferencia en puntos entre valores altos y bajos):")
        for x in v[:6]:
            L.append(f"  {x['variable']:22} {x['separa_pp']:+6.1f} pp "
                     f"(alto {x['alto']:.2f} vs bajo {x['bajo']:.2f})")
        muertas = [x["variable"] for x in v if abs(x["separa_pp"]) < 2]
        if muertas:
            L.append(f"  Sin poder separador (|diferencia| < 2 pp): {', '.join(muertas)}")

    p = await por_par(pool)
    if p:
        L.append("")
        L.append("POR PAR -- no todos se comportan igual:")
        for x in p:
            L.append(f"  {x['simbolo']:10} n={x['n']:4} acierto {x['acierto_pct']:5.1f}% "
                     f"mov {x['mov_medio_pct']:5.3f}% neto {x['neto_pct']:+7.2f}%")

    return NL.join(L)
