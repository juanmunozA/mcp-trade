"""
Superficie HTTP del sistema de materias primas: `python -m materias.api`

QUE SIRVE
─────────
El tablero y los datos que consume. Todo lectura. Este servicio mide, no
decide nada que alguien de afuera pueda disparar.

DE DONDE SALEN LOS DATOS
De `state/diario_materias.db`, el diario del capitulo 19.1. No se
recalcula nada por peticion: el backtest tarda cuatro minutos y
correrlo cada vez que alguien abre el navegador no tendria sentido. Lo
que se muestra es lo que el backtest guardo la ultima vez que corrio,
con su fecha visible para que nadie confunda una corrida vieja con el
estado actual.

DOS ORIGENES QUE NO SE MEZCLAN
    backtest   operaciones simuladas sobre historico
    sistema    senales emitidas en vivo por `materias.correr`

Promediarlos daria un numero sin significado: uno tiene resultado y el
otro todavia no. El tablero los separa siempre, y las metricas del
capitulo 18.7 se calculan solo sobre las cerradas.

SIN TOKEN, A PROPOSITO
No hay nada que proteger: son senales sobre precios publicos, sin claves
ni dinero, y el sistema no envia ordenes. Pedir un token obligaria a
manejarlo en el navegador para ver una tabla.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import pathlib
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from materias import datos as D
from materias import diario as DI
from materias import gestion as G
from materias import metricas as M

from materias import sesiones as S

log = logging.getLogger("materias.api")

UI = pathlib.Path(__file__).with_name("ui.html")
UI_COMPLETO = pathlib.Path(__file__).with_name("ui_completo.html")
"""
El tablero anterior, con los nueve modelos y el backtest.

No se borra porque sigue midiendo algo real -- 153 operaciones con su
embudo y su Monte Carlo -- pero deja de ser la portada. Lo que se mira
cada dia ahora es el piloto.
"""
NY = ZoneInfo("America/New_York")
NL = chr(10)
CRLF = chr(13) + chr(10)


def estado_actual() -> dict:
    """Donde esta el mercado ahora mismo, sin tocar la red."""
    ahora = datetime.now(NY)
    e = S.estado(ahora)
    return {
        "hora_ny": e["hora_ny"],
        "fecha": str(ahora.date()),
        "sesiones": e["sesiones"],
        "killzones": e["killzones"],
        "silver_bullet": e["silver_bullet"],
        "operable": e["operable"],
        "ventana": e["ventana_operable"],
        "proxima": S.proxima_operable(ahora),
        "riesgo_pct": G.RIESGO_POR_OPERACION,
        "activos": [{"simbolo": k, **v} for k, v in D.ACTIVOS.items()],
    }


def corrida() -> dict:
    """
    La ultima corrida de backtest, entera.

    Trae ya calculado todo lo que el tablero pinta: resumen, esquemas,
    modelos, ventanas, activos, calidad, confluencias, embudo, vetos,
    Monte Carlo y la curva de R acumulado.
    """
    c = DI.ultima_corrida()
    if not c:
        return {"hay": False}
    c["hay"] = True
    # El texto completo del informe se sirve aparte: pesa y casi nunca se
    # mira, pero cuando se mira se quiere entero.
    c.pop("texto", None)
    c["historial"] = DI.corridas(10)
    return c


informe = DI.informe
operaciones = DI.listar


def en_vivo() -> dict:
    """
    El forward test: lo que se emitio en vivo y como va.

    Separado del backtest siempre. Son las unicas operaciones que se
    emitieron ANTES de saber el resultado, y por eso son las unicas que
    pueden confirmar o desmentir lo que promete el backtest -- que es la
    comparacion que pide el paso 7 del capitulo 20.1.
    """
    from materias import papel as PA
    # Las dos fuentes conviven y hay que verlas juntas pero distinguidas:
    #   papel    GC=F por Yahoo, simulado. Corre en Render 24/5.
    #   broker   XAUUSD en Tickmill por MT5, ordenes de verdad. Corre en
    #            la maquina Windows que tenga la terminal abierta.
    # El MetaTrader5 de Python es solo Windows, asi que el segundo no
    # puede correr en Render. Comparten base, y por eso comparten tablero.
    todas = [o for o in DI.leer_estado(None) if o.get("origen") in ("papel", "broker")]
    vivas = [o for o in todas if o["estado"] in ("pendiente", "abierta")]
    cerradas = [o for o in todas
                if o["estado"] in ("ganancia", "perdida", "be")][-60:]
    vetadas = [o for o in DI.leer_estado(("vetada",))
               if o.get("origen") in ("sistema", "broker")][-40:]
    motivos = defaultdict(int)
    for v in vetadas:
        motivos[v.get("nota") or "?"] += 1
    res = PA.resumen()
    cerr = [o for o in todas
            if o["estado"] in ("ganancia", "perdida", "be") and o.get("r") is not None]
    res["metricas"] = (M.calcular([o["r"] for o in cerr],
                                  G.RIESGO_POR_OPERACION).dict() if cerr else None)
    # El desglose importa: una operacion del broker vale mas como
    # evidencia que una de papel, porque pago spread de verdad y se lleno
    # a un precio de verdad. Contarlas juntas sin decirlo las igualaria.
    res["por_origen"] = {}
    for og in ("papel", "broker"):
        suyas = [o["r"] for o in cerr if o.get("origen") == og]
        res["por_origen"][og] = {
            "cerradas": len(suyas),
            "vivas": sum(1 for o in todas if o.get("origen") == og
                         and o["estado"] in ("pendiente", "abierta")),
            "r_total": round(sum(suyas), 3),
        }
    return {
        "resumen": res,
        "vivas": list(reversed(vivas)),
        "cerradas": list(reversed(cerradas)),
        "vetadas": list(reversed(vetadas)),
        "motivos_veto": [{"motivo": k, "n": n}
                         for k, n in sorted(motivos.items(), key=lambda x: -x[1])],
    }


def planes(fecha: str = "") -> list[dict]:
    """Los planes del dia (cap. 3.2, paso 5), el mas reciente por activo."""
    out = DI.planes(fecha)
    for d in out:
        d["nombre"] = D.ACTIVOS.get(d["simbolo"], {}).get("nombre", d["simbolo"])
    return out


def dia_a_dia() -> list[dict]:
    """
    El resultado agrupado por dia, para ver si se concentra o se reparte.

    Un sistema cuyo resultado entero sale de dos dias buenos no es el
    mismo que uno que gana poquito todos los dias, aunque el total
    coincida. La guia no lo pide por su nombre, pero es la lectura que
    hace falta para juzgar el drawdown del capitulo 14.5.
    """
    ops = [o for o in operaciones(600, origen="backtest") if o.get("r") is not None]
    g: dict[str, list[float]] = defaultdict(list)
    for o in ops:
        g[o["fecha"]].append(o["r"])
    acum = 0.0
    out = []
    for f in sorted(g):
        r = sum(g[f])
        acum += r
        out.append({"fecha": f, "n": len(g[f]), "r": round(r, 3),
                    "acum": round(acum, 3)})
    return out


def piloto() -> dict:
    """
    El piloto de entrada forzada. Es lo principal del tablero ahora.

    Va aparte de todo lo demas porque mide otra cosa: no si el sistema
    sabe elegir cuando entrar, sino si sabe hacia donde va el precio. Los
    dos numeros que importan son el acierto de direccion contra el 50 %
    del azar, y si las entradas de confianza "alta" aciertan mas que las
    de "baja".
    """
    from materias import piloto as P
    r = P.resumen()
    filas = DI.leer_estado(None, origen=P.ORIGEN)
    filas.sort(key=lambda o: str(o.get("ts") or ""), reverse=True)
    out = []
    for o in filas[:40]:
        try:
            n = json.loads(o.get("nota") or "{}")
            n = n if isinstance(n, dict) else {}
        except (ValueError, TypeError):
            n = {}
        med = n.get("medicion") or {}
        out.append({
            "id": o["id"], "ts": o.get("ts"), "lado": o.get("lado"),
            "entrada": o.get("entrada"), "sl": o.get("sl"), "tp": o.get("tp"),
            "rr_objetivo": o.get("rr_objetivo"), "lotes": o.get("lotes"),
            "estado": o.get("estado"), "r": o.get("r"),
            "r_bruto": o.get("r_bruto"), "costo_r": o.get("costo_r"),
            "salida": o.get("salida"),
            "confianza": n.get("confianza"),
            "razonamiento": n.get("razonamiento"),
            "riesgos": n.get("riesgos"),
            "peso_noticias": n.get("peso_noticias"),
            "noticias": n.get("noticias"),
            "citas": o.get("confluencias") or [],
            "direccion_ok": med.get("direccion_ok"),
            "movimiento": med.get("movimiento"),
            "uso": n.get("uso") or {},
        })
    r["entradas_detalle"] = out
    r["cada_horas"] = P.CADA_HORAS
    r["horizonte_horas"] = P.HORIZONTE_HORAS
    return r


def notas() -> dict:
    """Lo que el agente ha aprendido de sus propias entradas (cap. 19.1)."""
    from materias import saber
    return {"version": saber.version(), "cuerpo": saber.leer(),
            "historial": saber.historial(8)}


def concentracion() -> dict:
    """
    ¿De donde sale el resultado? De muchas operaciones o de dos.

    Ninguna metrica del capitulo 18.7 responde esto directamente. El
    Sharpe se acerca -- por eso la guia lo pide -- pero un numero de 0,14
    no dice DONDE esta la cola. Aqui se ve.

    Importa porque cambia que se puede concluir. Un +78R repartido entre
    150 operaciones es un sistema; un +78R donde una sola operacion vale
    +42R es una operacion afortunada mas un sistema que empata. Y la
    segunda lectura no se arregla operando mas: se arregla midiendo mas,
    que es lo que el capitulo 18.7 pide con su «> 100, idealmente > 300».
    """
    ops = [o for o in operaciones(600, origen="backtest") if o.get("r") is not None]
    if not ops:
        return {}
    rs = sorted((o["r"] for o in ops), reverse=True)
    total = sum(rs)
    dias = dia_a_dia()
    mejor_dia = max(dias, key=lambda d: d["r"]) if dias else None
    mejor = max(ops, key=lambda o: o["r"])

    # Cuantas ganadoras hacen falta para juntar la mitad del beneficio
    # bruto. Con reparto parejo serian la mitad de las ganadoras; cuantas
    # menos sean, mas depende el resultado de que salgan justo esas.
    ganadoras = [r for r in rs if r > 0]
    bruto = sum(ganadoras)
    acum, cuantas = 0.0, 0
    for r in ganadoras:
        acum += r
        cuantas += 1
        if acum >= bruto / 2:
            break
    return {
        "total": round(total, 2),
        "n": len(ops),
        "mejor_operacion": {
            "r": round(mejor["r"], 2), "pct": round(100 * mejor["r"] / total, 1),
            "activo": mejor.get("nombre"), "setup": mejor.get("setup"),
            "fecha": mejor.get("fecha"), "rr_objetivo": mejor.get("rr_objetivo"),
        },
        "sin_la_mejor": round(total - mejor["r"], 2),
        "mejor_dia": ({"fecha": mejor_dia["fecha"], "r": round(mejor_dia["r"], 2),
                       "pct": round(100 * mejor_dia["r"] / total, 1),
                       "n": mejor_dia["n"]} if mejor_dia else None),
        "sin_el_mejor_dia": round(total - (mejor_dia["r"] if mejor_dia else 0), 2),
        "ganadoras": len(ganadoras),
        "ganadoras_para_la_mitad": cuantas,
        "mediana": round(sorted(o["r"] for o in ops)[len(ops) // 2], 3),
    }


def historial() -> list[dict]:
    """
    Las corridas guardadas, para comparar antes y despues de cada ajuste.

    Es el paso 8 del capitulo 20.1: «revisa y ajusta UN SOLO PARAMETRO A
    LA VEZ, nunca varios simultaneamente». Sin guardar la corrida
    anterior no hay contra que comparar, y el ajuste se vuelve fe.
    """
    out = []
    for c in DI.corridas(12):
        r = c.get("resumen") or {}
        out.append({"id": c["id"], "ts": c["ts"], "etiqueta": c["etiqueta"],
                    "esquema": c["esquema"], "n": r.get("n"),
                    "r_total": r.get("r_total"), "expectativa": r.get("expectativa"),
                    "profit_factor": r.get("profit_factor"),
                    "win_rate": r.get("win_rate"),
                    "costo_medio_r": r.get("costo_medio_r")})
    return out


def listones() -> list[dict]:
    """
    Cada metrica del capitulo 18.7 contra su liston, con el veredicto.

    Es la tabla que decide si el sistema puede pasar al paso 7 del
    proceso (forward test en demo). Mientras algo este en rojo, no.
    """
    c = DI.ultima_corrida()
    r = (c or {}).get("resumen") or {}
    if not r:
        return []
    mc = (c or {}).get("montecarlo") or {}
    def fila(nombre, valor, ok, liston, cita):
        return {"metrica": nombre, "valor": valor, "pasa": bool(ok),
                "liston": liston, "cita": cita}
    return [
        fila("Expectativa", f"{r['expectativa']:+.3f}R", r["expectativa"] > 0,
             "> 0 y > costos",
             "«Expectativa = (Win rate x Ganancia media) - (Loss rate x Perdida media)»"),
        fila("Profit factor", f"{r['profit_factor']:.2f}",
             1.3 <= r["profit_factor"] <= 3, "1,3 a 3",
             "«> 1,3 (bueno > 1,5); > 3 suele indicar sobreajuste»"),
        fila("Win rate vs equilibrio",
             f"{r['win_rate']:.1f}% vs {r['win_rate_equilibrio']:.1f}%",
             r["margen"] > 0, "por encima del equilibrio",
             "«Win rate de equilibrio = 1 / (1 + R:R)» (cap. 14.3)"),
        fila("Max drawdown", f"{r['max_drawdown_pct']:.1f}%",
             r["max_drawdown_pct"] < 20, "< 15-20 %",
             "«Tolerable psicologicamente; < 15-20 % para cuentas propias»"),
        fila("Recovery factor", f"{r['recovery_factor']:.2f}",
             r["recovery_factor"] > 3, "> 3",
             "«Beneficio neto / maximo drawdown»"),
        fila("Sharpe por operacion", f"{r['sharpe']:.2f}", r["sharpe"] > 1, "> 1",
             "«Rentabilidad ajustada por volatilidad»"),
        fila("Numero de operaciones", str(r["n"]), r["n"] >= 300, "> 100, ideal > 300",
             "«Tamano de la muestra > 100 (idealmente > 300) para conclusiones»"),
        fila("Peor racha", f"{r['peor_racha']} seguidas"
             + (f" · p95 simulado {mc.get('racha_p95')}" if mc else ""),
             r["peor_racha"] <= 8, "la que puedas soportar",
             "«Saber cuantas puedes soportar sin abandonar el sistema»"),
    ]


def _limpio(x):
    """
    Cambia inf y NaN por None, en todo el arbol.

    `json.dumps` los escribe como `Infinity` y `NaN` a secas, que Python
    relee sin quejarse pero NO son JSON valido: `JSON.parse` del
    navegador lanza excepcion y el tablero entero se queda en blanco.
    Un solo grupo de tres operaciones sin perdedoras bastaba para
    tumbarlo, y desde el servidor no se veia nada raro -- devolvia 200.
    """
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _limpio(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_limpio(v) for v in x]
    return x


def _json(obj) -> bytes:
    return json.dumps(_limpio(obj), default=str, ensure_ascii=False,
                      allow_nan=False).encode()


RUTAS = ["/ui", "/api/piloto", "/api/notas",
         "/api/estado", "/api/corrida", "/api/listones",
         "/api/concentracion", "/api/historial", "/api/operaciones",
         "/api/vivo", "/api/planes", "/api/dias", "/api/informe", "/salud"]


async def servir(puerto: int) -> None:
    async def atender(reader, writer):
        ruta = "?"
        try:
            linea = await asyncio.wait_for(reader.readline(), timeout=8)
            partes = linea.decode("latin-1").split()
            ruta = partes[1] if len(partes) > 1 else "/"
            while True:
                h = await asyncio.wait_for(reader.readline(), timeout=8)
                if not h.strip():
                    break
            camino, _, consulta = ruta.partition("?")
            q = dict(p.split("=", 1) for p in consulta.split("&") if "=" in p)
            camino = camino.rstrip("/") or "/"
            tipo, cuerpo = "application/json; charset=utf-8", b"{}"

            if camino in ("/", "/ui"):
                tipo = "text/html; charset=utf-8"
                cuerpo = UI.read_bytes() if UI.exists() else b"<p>sin tablero"
            elif camino == "/ui/completo":
                tipo = "text/html; charset=utf-8"
                cuerpo = (UI_COMPLETO.read_bytes() if UI_COMPLETO.exists()
                          else b"<p>sin tablero completo")
            elif camino == "/api/estado":
                cuerpo = _json(estado_actual())
            elif camino == "/api/corrida":
                cuerpo = _json(corrida())
            elif camino == "/api/piloto":
                cuerpo = _json(piloto())
            elif camino == "/api/notas":
                cuerpo = _json(notas())
            elif camino == "/api/concentracion":
                cuerpo = _json(concentracion())
            elif camino == "/api/historial":
                cuerpo = _json({"items": historial()})
            elif camino == "/api/listones":
                cuerpo = _json({"items": listones()})
            elif camino == "/api/operaciones":
                cuerpo = _json({"items": operaciones(
                    int(q.get("n", 200)), q.get("origen", ""), q.get("setup", ""),
                    q.get("simbolo", ""), q.get("ventana", ""))})
            elif camino == "/api/vivo":
                cuerpo = _json(en_vivo())
            elif camino == "/api/planes":
                cuerpo = _json({"items": planes(q.get("fecha", ""))})
            elif camino == "/api/dias":
                cuerpo = _json({"items": dia_a_dia()})
            elif camino == "/api/informe":
                cuerpo = _json({"texto": informe()})
            elif camino == "/salud":
                cuerpo = _json({"ok": True, "diario": DI.estado(),
                                "at": datetime.now(NY).isoformat(timespec="seconds")})
            else:
                cuerpo = _json({"error": "no existe", "rutas": RUTAS})

            cab = (f"HTTP/1.1 200 OK{NL}Content-Type: {tipo}{NL}"
                   f"Content-Length: {len(cuerpo)}{NL}"
                   f"Cache-Control: no-store{NL}"
                   f"Access-Control-Allow-Origin: *{NL}"
                   f"Connection: close{NL}{NL}").replace(NL, CRLF).encode()
            writer.write(cab + cuerpo)
            await writer.drain()
        except Exception as exc:  # noqa: BLE001
            log.warning("fallo sirviendo %s: %r", ruta, exc)
            with contextlib.suppress(Exception):
                err = _json({"error": repr(exc)[:200]})
                writer.write((f"HTTP/1.1 500 Internal Server Error{NL}"
                              f"Content-Type: application/json{NL}"
                              f"Content-Length: {len(err)}{NL}"
                              f"Connection: close{NL}{NL}")
                             .replace(NL, CRLF).encode() + err)
                await writer.drain()
        with contextlib.suppress(Exception):
            writer.close()

    srv = await asyncio.start_server(atender, "0.0.0.0", puerto)
    log.info("tablero en http://localhost:%d/ui", puerto)
    async with srv:
        await srv.serve_forever()


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    await servir(int(os.getenv("MATERIAS_PUERTO", "8790")))


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
