"""
Backtest del sistema completo: `python -m materias.backtest`

POR QUE ESTO VA ANTES QUE OPERAR
────────────────────────────────
Es el paso 6 del proceso de ocho pasos (cap. 20.1): «Backtest de 100-300
operaciones y calculo de metricas», antes del forward test y muy antes
del dinero real. La guia lo repite en el capitulo 11: las estrategias se
presentan como plantillas y «antes de usarlas con dinero real deben
probarse con backtesting».

QUE SE PRUEBA
Los nueve modelos -- la estrategia armada del capitulo 20.2 y los ocho
del capitulo 11 -- pasados por el checklist universal del 11.9, con el
filtro de noticias del 15, los limites de riesgo del 14.1 y los cuatro
esquemas de gestion del 13.2 corriendo en paralelo sobre las MISMAS
senales.

COMO SE SIMULA
Se recorre la historia vela a vela de 5M reconstruyendo lo que el sistema
habria visto en ese instante -- solo con velas anteriores, nunca
posteriores -- y se evalua el mismo codigo que corre en vivo. Que sea la
misma funcion es el punto: un backtest que reimplementa la logica mide
otra cosa.

LOS COSTOS ESTAN DENTRO
El liston de la guia es «win rate superior a ~35-38 % INCLUYENDO
COSTOS». Cada operacion paga spread mas comision, convertidos a R
dividiendo por su propia distancia al stop. Sin eso el resultado es
optimista de una forma que no se nota hasta que se opera de verdad.

LOS DOS SESGOS QUE HAY QUE DECLARAR
1. Cuando una vela de 5M toca SL y TP dentro de la misma vela no se sabe
   cual ocurrio primero sin datos de 1M. Se cuenta como PERDIDA. Es el
   supuesto conservador: contarlo como ganancia inflaria el resultado
   justo en las operaciones mas ajustadas.
2. La orden limite se da por llena cuando el precio TOCA el nivel. En la
   realidad una limite puede no llenarse si solo lo roza. Esto es
   optimista, y no hay forma de corregirlo sin datos de libro.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import timedelta

from materias import calidad as C
from materias import correlaciones as COR
from materias import datos as D
from materias import diario as DI
from materias import estructura as E
from materias import estrategia as ES
from materias import gestion as G
from materias import indicadores as I
from materias import metricas as M
from materias import sesiones as S

NL = chr(10)
CAPITAL = 10000.0
SALTO = 12
"""Velas que se saltan tras una senal, para no contar la misma tres veces."""

VENTANA_5M = 1500
"""
Cuantas velas de 5M se le pasan al sistema en cada instante simulado.

1.500 velas son unos cinco dias de sesion. Ningun modelo mira mas atras:
el mas largo usa las ultimas 60 velas (11.7) y el rango de Asia y los
niveles diarios salen de `diario`, no de aqui.

Es una optimizacion, no un cambio de reglas — pero se deja escrita
porque SI cambia un detalle: los maximos y minimos iguales (EQH/EQL) se
detectan sobre esa ventana. Liquidez de hace tres semanas no es liquidez
del dia, asi que acotarla es ademas mas fiel al capitulo 6.1, donde los
niveles que importan son los de sesion, dia y semana.

Sin esto el backtest reconstruia 6.000 velas en cada una de las ~30.000
iteraciones y tardaba media hora, que es demasiado para iterar.
"""


def _clave(motivo: str, largo: int = 62) -> str:
    """
    Normaliza el motivo quitando precios y niveles.

    Sin esto «no rompio el swing 6.53» y «... 89.80» cuentan como motivos
    distintos y el embudo real queda oculto detras de decenas de filas
    con n=1. Medir POR QUE no se opero vale tanto como medir las
    operaciones.
    """
    return re.sub(r"[-+]?\d[\d.,]*", "N", motivo).split("(")[0].strip()[:largo]


def _entra(m5: list[dict], desde_i: int, entrada: float,
           lado: str) -> int | None:
    """Cuando se llena la orden limite, o None si expira (cap. 20.2)."""
    limite = m5[desde_i]["dt"] + timedelta(minutes=ES.MINUTOS_VIGENCIA)
    for k in range(desde_i + 1, len(m5)):
        if m5[k]["dt"] > limite:
            return None
        if m5[k]["l"] <= entrada <= m5[k]["h"]:
            return k
    return None


async def correr(simbolos: list[str] | None = None,
                 guardar: bool = True, etiqueta: str = "") -> str:
    """
    Corre el backtest, imprime el informe y lo guarda en el diario.

    `guardar` persiste la corrida entera -- operaciones, embudo, esquemas,
    Monte Carlo -- para que el tablero la lea sin repetir la simulacion y
    para poder comparar corridas entre si. `etiqueta` es el nombre del
    cambio que se esta probando: el paso 8 del capitulo 20.1 exige
    «ajustar un solo parametro a la vez», y dejar por escrito cual fue es
    la unica forma de que dentro de un mes se sepa que se toco.
    """
    simbolos = simbolos or list(D.ACTIVOS)
    # Lo que se va a persistir, en paralelo al texto del informe.
    datos_out: dict = {"etiqueta": etiqueta}
    # Los auxiliares no se operan: se descargan para la SMT del cap. 15.5.
    datos = await D.cargar(simbolos + list(COR.AUXILIARES))
    L = ["BACKTEST · Sistema completo (caps. 11, 13, 14, 15, 16, 20)", ""]

    # Un registro por esquema de gestion: las mismas senales, cuatro
    # salidas distintas. El capitulo 13.2 lo pide asi -- «decide la
    # gestion con backtesting, no por sensacion».
    por_esquema: dict[str, list[dict]] = defaultdict(list)
    rechazos: dict[str, int] = defaultdict(int)
    embudo: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    vetos: dict[str, int] = defaultdict(int)
    confl: dict[str, int] = defaultdict(int)
    senales = expiradas = 0

    for s in simbolos:
        d = datos.get(s)
        if not d or len(d.get("m5", [])) < 200:
            continue
        m5 = d["m5"]
        costo_precio = D.costo(s)
        dia_actual = None
        est_dia = G.EstadoDia()
        i = 100
        while i < len(m5) - 1:
            ahora = m5[i]["dt"]
            if dia_actual != ahora.date():
                dia_actual = ahora.date()
                est_dia = G.EstadoDia(fecha=str(dia_actual))
            if not S.estado(ahora)["operable"]:
                i += 1
                continue
            if G.puede_operar(est_dia):
                i += 1
                continue

            recorte = {
                "m5": m5[max(0, i + 1 - VENTANA_5M):i + 1],
                "m15": [v for v in d.get("m15", []) if v["dt"] <= ahora],
                "h1": [v for v in d.get("h1", []) if v["dt"] <= ahora],
                "diario": [v for v in d.get("diario", []) if v["dt"] <= ahora],
            }
            recorte["h4"] = D._agrupar(recorte["h1"], 4)
            ctx = D.contexto(s, recorte, ahora)

            sen, intentos = ES.evaluar_todo(s, ctx)
            for it in intentos:
                if not it.hay:
                    embudo[it.setup][_clave(it.motivo)] += 1
            if not sen.hay:
                rechazos[_clave(sen.motivo, 70)] += 1
                i += 1
                continue

            # ── El checklist universal (cap. 11.9) ────────────────────
            lect = I.leer(ctx)
            div = COR.buscar(s, sen.lado, datos, ahora)
            ev = C.evaluar(sen, ctx, est_dia, CAPITAL, lect, div)
            if not ev.aprobada:
                vetos[_clave(ev.veto, 70)] += 1
                i += 1
                continue
            for c in ev.confluencias:
                confl[_clave(c, 50)] += 1

            # ── Ejecucion ─────────────────────────────────────────────
            k = _entra(m5, i, sen.entrada, sen.lado)
            senales += 1
            if k is None:
                expiradas += 1
                i += SALTO
                continue

            dist = abs(sen.entrada - sen.sl)
            costo_r = costo_precio / dist if dist > 0 else 0.0
            # El trailing por ATR del esquema escalonado (cap. 13.4).
            atr5 = E.atr(E.velas(recorte["m5"]))
            a15_actual = E.atr(E.velas(recorte["m15"]))

            base_r = None
            for esq in G.ESQUEMAS:
                r = G.simular_salida(m5, k - 1, sen.lado, sen.entrada,
                                     sen.sl, sen.tp, esq, atr5)
                if r["estado"] == "abierta":
                    continue
                neto = round(r["r"] - costo_r, 4)
                fila = {"simbolo": s, "dt": str(ahora), "lado": sen.lado,
                        "setup": sen.setup, "ventana": sen.ventana,
                        "nivel": sen.nombre_nivel, "rr_obj": sen.rr,
                        # Los niveles crudos: sin ellos no se puede
                        # auditar una operacion rara, y las hay. Un
                        # objetivo de 42R no se ve en el promedio.
                        "entrada": sen.entrada, "sl": sen.sl, "tp": sen.tp,
                        "dist_sl": round(abs(sen.entrada - sen.sl), 6),
                        "atr15": round(a15_actual, 6),
                        "calidad": ev.puntos, "lotes": ev.lotes,
                        "confluencias": [_clave(c, 50) for c in ev.confluencias],
                        "costo_r": round(costo_r, 4), "r_bruto": r["r"],
                        "r": neto, "estado": r["estado"]}
                por_esquema[esq.nombre].append(fila)
                if esq.nombre == "todo_en_tp":
                    base_r = neto
            if base_r is not None:
                est_dia.registrar(base_r)
            i += SALTO
        L.append(f"{D.ACTIVOS[s]['nombre']:14} {len(m5)} velas de 5M")

    L.append("")
    L.append(f"senales aprobadas: {senales}   "
             f"expiraron sin entrar: {expiradas}   "
             f"operadas: {senales - expiradas}")

    # ── Comparacion de esquemas (cap. 13.2) ───────────────────────────
    L.append("")
    L.append("LOS CUATRO ESQUEMAS DE GESTION SOBRE LAS MISMAS SENALES")
    L.append("  «Cerrar parcial y mover a BE reduce el R promedio ganador.")
    L.append("   Decide la gestion con backtesting, no por sensacion» (13.2)")
    L.append("")
    L.append(f"  {'esquema':14} {'n':>4} {'wr':>7} {'R:R':>6} {'equil':>7} "
             f"{'R total':>9} {'exp':>8} {'PF':>6} {'DD':>7}")
    metricas_esq: dict[str, M.Resultado] = {}
    datos_out["esquemas"] = []
    for esq in G.ESQUEMAS:
        filas = por_esquema.get(esq.nombre, [])
        if not filas:
            continue
        m = M.calcular([f["r"] for f in filas], G.RIESGO_POR_OPERACION)
        metricas_esq[esq.nombre] = m
        datos_out["esquemas"].append({"nombre": esq.nombre, "nota": esq.nota,
                                      **m.dict()})
        L.append(f"  {esq.nombre:14} {m.n:4} {m.win_rate:6.1f}% "
                 f"{m.rr_efectivo:6.2f} {m.win_rate_equilibrio:6.1f}% "
                 f"{m.r_total:+9.2f} {m.expectativa:+8.3f} "
                 f"{m.profit_factor:6.2f} {m.max_drawdown_pct:6.1f}%")

    if not metricas_esq:
        L.append("  sin operaciones cerradas")
        return NL.join(L)

    mejor = max(metricas_esq, key=lambda k: metricas_esq[k].expectativa)
    L.append("")
    L.append(f"  -> el mejor por expectativa es «{mejor}». "
             "Todo lo que sigue usa ese.")
    base = por_esquema[mejor]
    datos_out["esquema"] = mejor
    datos_out["n"] = len(base)

    # ── Metricas completas (cap. 18.7) ────────────────────────────────
    m = metricas_esq[mejor]
    # `senales` y `expiradas` van dentro de `resumen` y no sueltos: la
    # tabla `corridas` guarda columnas fijas, y un campo suelto que no
    # tiene columna se pierde en silencio -- que es justo lo que paso la
    # primera vez y el tablero mostraba "undefined senales aprobadas".
    datos_out["resumen"] = {
        **m.dict(),
        "senales": senales, "expiradas": expiradas,
        "costo_medio_r": round(sum(f["costo_r"] for f in base) / len(base), 4)}
    L.append("")
    L.extend(M.informe(m, G.RIESGO_POR_OPERACION))
    L.append("")
    L.append(f"  costo medio        {sum(f['costo_r'] for f in base)/len(base):.3f}R "
             "por operacion (spread + comision, ya descontado)")

    mc = M.monte_carlo([f["r"] for f in base])
    datos_out["montecarlo"] = mc
    if mc:
        L.append("")
        L.append("MONTE CARLO  (cap. 18.6: «estimar peores rachas y drawdown»)")
        L.append(f"  2.000 simulaciones sobre las mismas {m.n} operaciones")
        L.append("")
        L.append("  Reordenando la muestra — cuanto pudo doler el mismo resultado:")
        L.append(f"    drawdown mediano  {mc['drawdown_mediano_r']:.2f}R"
                 f"   p95 {mc['drawdown_p95_r']:.2f}R"
                 f"   peor {mc['peor_drawdown_r']:.2f}R")
        L.append(f"    racha perdedora   p95 {mc['racha_p95']} seguidas"
                 f"   peor {mc['peor_racha_simulada']}")
        L.append("")
        L.append("  Remuestreando con reemplazo — que rango de resultados cabe esperar:")
        L.append(f"    resultado         p05 {mc['resultado_p05_r']:+.2f}R"
                 f"   mediano {mc['resultado_mediano_r']:+.2f}R"
                 f"   p95 {mc['resultado_p95_r']:+.2f}R")
        L.append(f"    probabilidad de terminar en perdida: {mc['prob_perder']:.1%}")
        L.append(f"    drawdown p95      {mc['drawdown_p95_bootstrap_r']:.2f}R")

    def _grupo(clave: str, titulo: str, ancho: int = 24,
               guardar_en: str = "") -> None:
        g: dict[str, list[float]] = defaultdict(list)
        for f in base:
            g[str(f[clave])].append(f["r"])
        L.append("")
        L.append(titulo)
        filas = []
        for k, lst in sorted(g.items(), key=lambda x: -sum(x[1])):
            mm = M.calcular(lst, G.RIESGO_POR_OPERACION)
            nombre = D.ACTIVOS[k]["nombre"] if k in D.ACTIVOS else k
            marca = "" if mm.n >= 30 else "   (muestra corta)"
            L.append(f"  {nombre:{ancho}} n={mm.n:3}  wr {mm.win_rate:5.1f}%  "
                     f"{mm.r_total:+7.2f}R  exp {mm.expectativa:+.3f}R{marca}")
            filas.append({"clave": k, "nombre": nombre, **mm.dict()})
        if guardar_en:
            datos_out[guardar_en] = filas

    _grupo("setup", "POR MODELO  (cual de los nueve aporta y cual hace ruido)",
           24, "modelos")
    _grupo("ventana", "POR VENTANA", 14, "ventanas")
    _grupo("simbolo", "POR ACTIVO", 14, "activos")

    # ── ¿Sirven las confluencias? (cap. 11.9) ─────────────────────────
    L.append("")
    L.append("POR CALIDAD  (confluencias del checklist 11.9)")
    g: dict[int, list[float]] = defaultdict(list)
    for f in base:
        g[int(f["calidad"])].append(f["r"])
    datos_out["calidad"] = []
    for k in sorted(g):
        mm = M.calcular(g[k], G.RIESGO_POR_OPERACION)
        L.append(f"  {k} confluencias        n={mm.n:3}  wr {mm.win_rate:5.1f}%  "
                 f"{mm.r_total:+7.2f}R  exp {mm.expectativa:+.3f}R")
        datos_out["calidad"].append({"confluencias": k, **mm.dict()})
    L.append("  Si la expectativa NO sube con las confluencias, el checklist")
    L.append("  no esta discriminando y hay que revisarlo, no confiarlo.")
    L.append("  OJO: esta tabla mezcla modelos. Si uno aporta mucho mas que")
    L.append("  otro y ademas acumula menos confluencias, la pendiente sale")
    L.append("  al reves sin que las confluencias tengan nada que ver.")
    L.append("  La tabla de abajo controla eso; esta no.")

    # ── ¿Cual confluencia aporta y cual estorba? ──────────────────────
    # Contar confluencias sin distinguirlas supone que todas valen lo
    # mismo. Esto lo mide: para cada una, la expectativa de las
    # operaciones que la tenian contra la de las que no. Si la diferencia
    # es negativa, esa confluencia esta empeorando las senales y sumarla
    # al puntaje es contraproducente.
    #
    # La comparacion va ESTRATIFICADA POR MODELO. Comparar «con esta
    # confluencia» contra «sin ella» sobre toda la muestra mezclada
    # medía otra cosa: si un modelo aporta +0,9R y otro +0,2R, y sus
    # confluencias tipicas son distintas, la tabla termina midiendo cual
    # modelo predomina en cada grupo, no si la confluencia sirve.
    #
    # Estratificar lo arregla: se compara dentro de cada modelo, y
    # despues se promedian las diferencias pesando por cuantas
    # operaciones aporto cada uno. Es el control de confusion mas simple
    # que hay, y sin el la tabla anterior era literalmente enganosa.
    L.append("")
    L.append("CADA CONFLUENCIA POR SEPARADO  (con ella vs sin ella, dentro del mismo modelo)")
    L.append(f"  {'confluencia':44} {'n con':>6} {'exp con':>9} "
             f"{'n sin':>6} {'exp sin':>9} {'delta':>8}")
    todas_c = sorted({c for f in base for c in f["confluencias"]})
    por_setup_filas: dict[str, list[dict]] = defaultdict(list)
    for f in base:
        por_setup_filas[f["setup"]].append(f)
    filas_c = []
    for c in todas_c:
        peso = 0
        suma = 0.0
        n_con = n_sin = 0
        e_con = e_sin = 0.0
        for _mod, filas in por_setup_filas.items():
            con = [f["r"] for f in filas if c in f["confluencias"]]
            sin = [f["r"] for f in filas if c not in f["confluencias"]]
            if len(con) < 5 or len(sin) < 5:
                continue
            a_ = M.calcular(con, G.RIESGO_POR_OPERACION)
            b_ = M.calcular(sin, G.RIESGO_POR_OPERACION)
            w = len(con)
            suma += (a_.expectativa - b_.expectativa) * w
            peso += w
            n_con += a_.n
            n_sin += b_.n
            e_con += a_.expectativa * a_.n
            e_sin += b_.expectativa * b_.n
        if peso < 10:
            continue
        filas_c.append((suma / peso, c, n_con, e_con / max(n_con, 1),
                        n_sin, e_sin / max(n_sin, 1)))
    if not filas_c:
        L.append("  ninguna confluencia tiene n>=5 a los dos lados dentro de un")
        L.append("  mismo modelo. Sin eso, cualquier comparacion mezcla modelos")
        L.append("  y mide la mezcla, no la confluencia.")
    datos_out["confluencias"] = []
    for delta, c, nc, ec, ns, es in sorted(filas_c, reverse=True):
        L.append(f"  {c[:44]:44} {nc:6} {ec:+9.3f} {ns:6} {es:+9.3f} {delta:+8.3f}")
        datos_out["confluencias"].append(
            {"nombre": c, "n_con": nc, "exp_con": round(ec, 4),
             "n_sin": ns, "exp_sin": round(es, 4), "delta": round(delta, 4)})
    if filas_c:
        buenas = [c for d, c, *_ in filas_c if d > 0.05]
        malas = [c for d, c, *_ in filas_c if d < -0.05]
        L.append("")
        if buenas:
            L.append(f"  aportan: {len(buenas)} de {len(filas_c)}")
        if malas:
            L.append(f"  RESTAN:  {', '.join(m[:40] for m in malas)}")
            L.append("")
            L.append("  QUE HACER CON ESTO")
            L.append("  Con estas muestras (n entre 5 y 60 por celda) la diferencia")
            L.append("  no distingue senal de ruido, asi que NO es motivo para")
            L.append("  quitar confluencias del checklist: el capitulo 11.9 las pide")
            L.append("  y una medicion de 20 operaciones no derriba eso.")
            L.append("  Lo que si dice es que el puntaje no sirve todavia para")
            L.append("  FILTRAR. Se usa solo para ORDENAR cuando hay mas senales")
            L.append("  que cupo de riesgo, que es para lo unico que esta puesto.")
            L.append("  Volver a mirar esta tabla con n>300 (cap. 18.7).")

    L.append("")
    L.append("CONFLUENCIAS MAS FRECUENTES EN LAS SENALES APROBADAS")
    for k, n in sorted(confl.items(), key=lambda x: -x[1])[:10]:
        L.append(f"  {n:6}  {k}")

    if vetos:
        L.append("")
        L.append("VETADAS POR EL CHECKLIST  (senal buena, contexto malo)")
        for k, n in sorted(vetos.items(), key=lambda x: -x[1])[:10]:
            L.append(f"  {n:6}  {k}")

    L.append("")
    L.append("POR QUE NO SE OPERO  (motivo del modelo que llego mas lejos)")
    for k, n in sorted(rechazos.items(), key=lambda x: -x[1])[:10]:
        L.append(f"  {n:6}  {k}")

    L.append("")
    L.append("EMBUDO DE CADA MODELO  (donde muere; sus tres motivos mayores)")
    datos_out["embudo"] = []
    for mod in sorted(embudo, key=lambda x: -sum(embudo[x].values())):
        L.append(f"  {mod}   {sum(embudo[mod].values())} rechazos")
        motivos = sorted(embudo[mod].items(), key=lambda x: -x[1])
        for k, n in motivos[:3]:
            L.append(f"        {n:6}  {k}")
        datos_out["embudo"].append(
            {"modelo": mod, "rechazos": sum(embudo[mod].values()),
             "motivos": [{"motivo": k, "n": n} for k, n in motivos[:6]]})
    datos_out["vetos"] = [{"motivo": k, "n": n}
                          for k, n in sorted(vetos.items(), key=lambda x: -x[1])]

    # La curva de R acumulado: es la que decide si el sistema sirve.
    acum = 0.0
    curva = []
    for f in base:
        acum += f["r"]
        curva.append({"t": f["dt"], "r": round(acum, 3), "op": round(f["r"], 3),
                      "simbolo": f["simbolo"], "setup": f["setup"]})
    datos_out["serie"] = curva

    if guardar:
        # Las operaciones se reemplazan -- son las mismas sobre los mismos
        # datos, y acumularlas duplicaria la muestra y con ella todas las
        # metricas. La corrida en si se acumula, para comparar antes y
        # despues de cada ajuste (cap. 20.1, paso 8).
        DI.borrar_backtest()
        for f in base:
            DI.guardar({
                "ts": f["dt"], "fecha": f["dt"][:10], "simbolo": f["simbolo"],
                "nombre": D.ACTIVOS.get(f["simbolo"], {}).get("nombre", f["simbolo"]),
                "ventana": f["ventana"], "setup": f["setup"], "lado": f["lado"],
                "entrada": f["entrada"], "sl": f["sl"], "tp": f["tp"],
                "rr_objetivo": f["rr_obj"], "lotes": f["lotes"],
                "riesgo_pct": G.RIESGO_POR_OPERACION,
                "confluencias": f["confluencias"], "calidad": int(f["calidad"]),
                "estado": f["estado"], "r_bruto": f["r_bruto"],
                "costo_r": f["costo_r"], "r": f["r"], "esquema": mejor,
                "origen": "backtest",
            })
        datos_out["texto"] = NL.join(L)
        DI.guardar_corrida(datos_out)
        L.append("")
        from materias import almacen as AL
        L.append(f"guardado en el diario: {len(base)} operaciones "
                 f"+ la corrida completa  ->  {AL.destino()}")
    return NL.join(L)


async def main() -> None:
    print(await correr())


if __name__ == "__main__":
    asyncio.run(main())
