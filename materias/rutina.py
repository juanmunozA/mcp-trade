"""
El analisis top-down y el plan del dia. Capitulo 3.2.

LA RUTINA, LITERAL
──────────────────
1. Semanal/diario: ¿Cual es la tendencia? ¿Donde estan PWH/PWL y
   PDH/PDL? ¿Hay un FVG u OB diario cerca? Define un sesgo: alcista,
   bajista o neutral (si es neutral, REDUCE TAMANO O NO OPERES).
2. 4H: marca las zonas de interes (OB, FVG, soportes/resistencias) y la
   liquidez obvia por encima y por debajo.
3. 1H: marca maximos y minimos de la sesion asiatica, de Londres y del
   dia anterior. Define HACIA QUE LIQUIDEZ es mas probable que se mueva
   el precio.
4. Calendario economico: revisa noticias de alto impacto del dia. Anota
   horarios en los que NO vas a abrir posiciones.
5. Plan: escribe escenarios "si ... entonces ...".
6. 15M/5M/1M: solo en la killzone y en la zona marcada, espera el gatillo
   definido. SI NO APARECE, NO HAY OPERACION.

POR QUE ESTO ES CODIGO Y NO UN DOCUMENTO
El paso 5 es el que importa: «escribe escenarios». Un plan escrito antes
de la sesion es lo que impide decidir en caliente. Que lo escriba el
sistema tiene una ventaja sobre que lo escriba una persona: queda
registrado con hora, y despues se puede comparar el plan con lo que
efectivamente paso. Eso es el registro que pide el capitulo 19.1.

El paso 6 no esta aqui -- es lo que hacen los nueve modelos. Esta rutina
llega hasta el plan y se detiene, igual que la guia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from materias import datos as D
from materias import estructura as E
from materias import fases as F
from materias import indicadores as I
from materias import noticias as N
from materias import perfil as P
from materias import sesiones as S


@dataclass
class Plan:
    simbolo: str = ""
    nombre: str = ""
    hora: str = ""
    sesgo: str = "neutral"
    confianza: str = "baja"
    fase: str = ""
    niveles: dict = field(default_factory=dict)
    zonas: list[dict] = field(default_factory=list)
    objetivo_liquidez: str = ""
    no_operar: list[str] = field(default_factory=list)
    escenarios: list[str] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)

    def dict(self) -> dict:
        return self.__dict__.copy()


def _sesgo(ctx: dict) -> tuple[str, str, list[str]]:
    """
    Paso 1 y 2: el sesgo de diario y 4H, y cuanto se confian entre si.

    «Define un sesgo: alcista, bajista o NEUTRAL (si es neutral, reduce
    tamano o no operes).» El caso neutral no es un fallo del analisis: es
    una conclusion valida y la guia le da instrucciones propias.
    """
    notas = []
    vd = E.velas(ctx.get("diario", []))
    v4 = E.velas(ctx.get("h4", []))
    ed = E.leer_estructura(vd) if len(vd) >= 20 else None
    e4 = E.leer_estructura(v4) if len(v4) >= 20 else None
    td = ed.tendencia if ed else "rango"
    t4 = e4.tendencia if e4 else "rango"
    notas.append(f"diario: {td}" + (f" (ultimo {ed.ultimo_evento})" if ed and ed.ultimo_evento else ""))
    notas.append(f"4H: {t4}" + (f" (ultimo {e4.ultimo_evento})" if e4 and e4.ultimo_evento else ""))
    if td == t4 and td != "rango":
        return td, "alta", notas
    if t4 != "rango" and td == "rango":
        notas.append("el diario esta en rango: el sesgo lo pone el 4H, con menos peso")
        return t4, "media", notas
    if td != "rango" and t4 == "rango":
        notas.append("4H lateral dentro de un diario con direccion: esperar definicion")
        return td, "baja", notas
    if td != t4 and "rango" not in (td, t4):
        notas.append(f"diario {td} contra 4H {t4}: conflicto de temporalidades")
        return "neutral", "baja", notas
    return "neutral", "baja", notas


def _zonas(ctx: dict, sesgo: str) -> list[dict]:
    """
    Paso 2: «marca las zonas de interes (OB, FVG, soportes/resistencias)».

    Solo las que siguen vivas -- sin mitigar y sin invalidar -- porque
    una zona ya consumida no es una zona: es historia.
    """
    out = []
    v4 = E.velas(ctx.get("h4", []))
    if len(v4) >= 20:
        a4 = E.atr(v4)
        for o in E.order_blocks(v4, a4):
            if not o.mitigado:
                out.append({"tf": "4H", "tipo": f"ob_{o.tipo}",
                            "piso": round(o.piso, 4), "tope": round(o.tope, 4),
                            "mt": round(o.mean_threshold, 4)})
        for f in E.fvgs(v4, a4 * 0.3):
            if not f.invalidado and not f.mitigado:
                out.append({"tf": "4H", "tipo": f"fvg_{f.tipo}",
                            "piso": round(f.piso, 4), "tope": round(f.tope, 4),
                            "ce": round(f.ce, 4)})
    vd = E.velas(ctx.get("diario", []))
    if len(vd) >= 20:
        ad = E.atr(vd)
        for f in E.fvgs(vd, ad * 0.3):
            if not f.invalidado and not f.mitigado:
                out.append({"tf": "1D", "tipo": f"fvg_{f.tipo}",
                            "piso": round(f.piso, 4), "tope": round(f.tope, 4)})
    # A favor del sesgo primero: son las que se van a operar.
    quiere = "alcista" if sesgo == "alcista" else "bajista"
    out.sort(key=lambda z: (0 if quiere in z["tipo"] else 1))
    return out[:8]


def _draw(ctx: dict, sesgo: str) -> str:
    """
    Paso 3: «define HACIA QUE LIQUIDEZ es mas probable que se mueva el precio».

    Es la pregunta del «draw on liquidity» del capitulo 6.1. La respuesta
    por defecto es la liquidez sin barrer que queda en direccion del
    sesgo, y la mas cercana primero: el precio busca lo que tiene mas a
    mano antes que lo lejano.
    """
    precio = ctx.get("precio", 0)
    n, a, lon = ctx.get("niveles", {}), ctx.get("asia", {}), ctx.get("londres", {})
    cand = []
    for nom, val in (("PDH", n.get("pdh")), ("PDL", n.get("pdl")),
                     ("PWH", n.get("pwh")), ("PWL", n.get("pwl")),
                     ("maximo de Asia", a.get("alto")), ("minimo de Asia", a.get("bajo")),
                     ("maximo de Londres", lon.get("alto")),
                     ("minimo de Londres", lon.get("bajo"))):
        if val is None:
            continue
        arriba = val > precio
        # Con sesgo neutral no hay una direccion que privilegiar, asi que
        # se listan los dos lados: el capitulo 6.1 pregunta «hacia que
        # liquidez», y la respuesta honesta cuando no hay sesgo es «hay
        # liquidez arriba y abajo, y el dia decidira cual busca».
        a_favor = ((sesgo == "alcista" and arriba)
                   or (sesgo == "bajista" and not arriba))
        if sesgo == "neutral" or a_favor:
            cand.append((nom, val, abs(val - precio), arriba))
    if not cand:
        return "sin liquidez clara identificable"
    cand.sort(key=lambda x: x[2])
    if sesgo == "neutral":
        arr = next((c for c in cand if c[3]), None)
        aba = next((c for c in cand if not c[3]), None)
        partes = []
        if arr:
            partes.append(f"arriba {arr[0]} ({arr[1]:.4f})")
        if aba:
            partes.append(f"abajo {aba[0]} ({aba[1]:.4f})")
        return " · ".join(partes) + "  — sin sesgo, el dia decide cual barre primero"
    nom, val = cand[0][0], cand[0][1]
    resto = (", luego " + ", ".join(f"{c[0]} ({c[1]:.4f})" for c in cand[1:3])
             if len(cand) > 1 else "")
    return f"{nom} en {val:.4f}{resto}"


def _escenarios(ctx: dict, sesgo: str, draw: str) -> list[str]:
    """
    Paso 5: «escribe escenarios "si ... entonces ..."».

    El ejemplo que la guia da, literal: «Si el precio barre el minimo de
    Asia en la killzone de Londres y hace CHoCH alcista en 5M, busco
    compras en el FVG con objetivo en el maximo de Asia».

    Se generan dos: el principal a favor del sesgo y el alternativo, que
    es el que dice que hacer si el mercado va al otro lado. Tener escrito
    el alternativo es lo que evita improvisar cuando el plan falla.
    """
    a = ctx.get("asia", {})
    n = ctx.get("niveles", {})
    out = []
    if not a.get("alto"):
        return ["sin rango de Asia todavia: el plan se escribe al cerrar Asia (04:00 ET)"]

    if sesgo == "alcista":
        out.append(
            f"SI el precio barre el minimo de Asia ({a['bajo']:.4f}) en la "
            f"killzone de Londres y hace CHoCH alcista en 5M, ENTONCES compras "
            f"en el FVG del desplazamiento, SL bajo la barrida, objetivo en el "
            f"maximo de Asia ({a['alto']:.4f}) y despues en {draw}")
        out.append(
            "SI en cambio cierra con cuerpo bajo el minimo de Asia y no "
            "recupera en 60 min, ENTONCES el sesgo alcista queda invalidado "
            "para el dia: no se compra y se espera al PDL"
            + (f" ({n['pdl']:.4f})" if "pdl" in n else ""))
    elif sesgo == "bajista":
        out.append(
            f"SI el precio barre el maximo de Asia ({a['alto']:.4f}) en la "
            f"killzone de Londres y hace CHoCH bajista en 5M, ENTONCES ventas "
            f"en el FVG del desplazamiento, SL sobre la barrida, objetivo en el "
            f"minimo de Asia ({a['bajo']:.4f}) y despues en {draw}")
        out.append(
            "SI en cambio cierra con cuerpo sobre el maximo de Asia y se "
            "sostiene, ENTONCES el sesgo bajista queda invalidado para el dia"
            + (f": se espera al PDH ({n['pdh']:.4f})" if "pdh" in n else ""))
    else:
        out.append(
            f"Sesgo NEUTRAL. La guia dice: «reduce tamano o no operes». "
            f"SI el precio barre un extremo de Asia ({a['bajo']:.4f} / "
            f"{a['alto']:.4f}) y hace CHoCH en 5M, ENTONCES se puede operar el "
            f"giro con la mitad del riesgo habitual; si no, no hay operacion")
    out.append(
        "EN CUALQUIER CASO: solo dentro de killzone, solo con objetivo a 2R "
        "o mas, maximo 2 operaciones y parar tras 2 perdidas (cap. 14.1)")
    return out


def analizar(simbolo: str, d: dict, ahora: datetime) -> Plan:
    """La rutina completa sobre un activo, en el orden del capitulo 3.2."""
    ctx = D.contexto(simbolo, d, ahora)
    p = Plan(simbolo=simbolo, nombre=D.ACTIVOS.get(simbolo, {}).get("nombre", simbolo),
             hora=str(ahora))

    # 1 y 2 — sesgo y zonas.
    p.sesgo, p.confianza, p.notas = _sesgo(ctx)
    p.zonas = _zonas(ctx, p.sesgo)

    # Fase del mercado (cap. 5), que decide que modelos aplican hoy.
    v5 = E.velas(ctx["m5"])
    # La fase se lee en 1H, asi que el volumen que la acompana tambien
    # tiene que ser el de 1H: los climax del capitulo 5.1 se miden contra
    # la media de la MISMA temporalidad que se esta clasificando.
    h1 = ctx.get("h1", [])
    fase = F.leer(E.velas(h1), [v.get("v", 0) or 0 for v in h1])
    amd = F.power_of_3(ctx["m5"], ctx.get("asia", {}), ahora, p.sesgo)
    p.fase = f"{fase.nombre} · AMD en {amd.fase}"
    if fase.eventos:
        p.notas.extend(fase.eventos)
    p.notas.append(amd.detalle)
    if not fase.permite_continuacion:
        p.notas.append("en rango: los modelos de continuacion (11.2, 11.3) no "
                       "aplican hoy; «es la fase donde mas cuentas se pierden»")

    # 3 — niveles.
    p.niveles = {"asia": ctx.get("asia", {}), "londres": ctx.get("londres", {}),
                 **ctx.get("niveles", {}), "tdo": ctx.get("tdo")}
    lect = I.leer(ctx)
    if lect.pivotes:
        p.niveles["pivotes"] = lect.pivotes
    perf = P.construir([v for v in ctx["m5"] if v["dt"].date() == ahora.date()])
    if perf.poc:
        p.niveles["poc"] = perf.poc
        p.niveles["value_area"] = [perf.val, perf.vah]
        p.notas.append(f"precio {P.sesgo_value_area(perf, ctx['precio'])} "
                       "respecto al valor negociado hoy")
    p.objetivo_liquidez = _draw(ctx, p.sesgo)

    # 4 — calendario.
    p.no_operar = N.agenda(ahora.date())

    # 5 — el plan escrito.
    p.escenarios = _escenarios(ctx, p.sesgo, p.objetivo_liquidez)

    # Contexto de indicadores, como filtro de sesgo (tabla 16.3).
    if lect.ema200_1h:
        lado = "sobre" if lect.precio_sobre_ema200 else "bajo"
        p.notas.append(f"precio {lado} la EMA 200 de 1H ({lect.ema200_1h})")
    if lect.adx:
        p.notas.append(f"ADX {lect.adx['adx']} -> {lect.adx['regimen']}, "
                       f"dominio de {lect.adx['dominio']}")
    if lect.bollinger.get("squeeze"):
        p.notas.append("bandas de Bollinger en squeeze: expansion proxima")
    if len(v5) > 20:
        vr = E.volumen_relativo(v5)
        if vr >= 1.5:
            p.notas.append(f"volumen {vr:.1f}x sobre su media en la ultima vela")
    return p


def texto(p: Plan) -> str:
    """El plan en la forma en que se lee antes de la sesion."""
    L = [f"PLAN DEL DIA · {p.nombre} ({p.simbolo})  —  {p.hora}",
         "",
         f"1. SESGO       {p.sesgo.upper()}  (confianza {p.confianza})"]
    for n in p.notas[:2]:
        L.append(f"               {n}")
    L.append(f"   FASE        {p.fase}")
    L.append("")
    L.append("2. ZONAS DE INTERES (sin mitigar)")
    if p.zonas:
        for z in p.zonas:
            extra = f"  mt={z['mt']}" if "mt" in z else (f"  ce={z['ce']}" if "ce" in z else "")
            L.append(f"   {z['tf']:3} {z['tipo']:16} {z['piso']} - {z['tope']}{extra}")
    else:
        L.append("   ninguna viva en 4H/1D")
    L.append("")
    L.append("3. NIVELES Y DRAW ON LIQUIDITY")
    a = p.niveles.get("asia", {})
    if a.get("alto"):
        L.append(f"   Asia      {a['bajo']:.4f} - {a['alto']:.4f}")
    lon = p.niveles.get("londres", {})
    if lon.get("alto"):
        L.append(f"   Londres   {lon['bajo']:.4f} - {lon['alto']:.4f}")
    for k in ("pdl", "pdh", "pwl", "pwh", "tdo", "poc"):
        if p.niveles.get(k) is not None:
            L.append(f"   {k.upper():9} {p.niveles[k]}")
    L.append(f"   -> el precio busca: {p.objetivo_liquidez}")
    L.append("")
    L.append("4. HORARIOS EN LOS QUE NO SE ABREN POSICIONES")
    L.extend(p.no_operar)
    L.append("")
    L.append("5. ESCENARIOS")
    for e in p.escenarios:
        L.append(f"   · {e}")
    if len(p.notas) > 2:
        L.append("")
        L.append("   OTRAS NOTAS")
        for n in p.notas[2:]:
            L.append(f"   · {n}")
    L.append("")
    L.append(f"6. GATILLO: solo en killzone y en la zona marcada. "
             f"Proxima ventana: {S.proxima_operable(datetime.fromisoformat(p.hora))}")
    L.append("   «Si no aparece, NO HAY OPERACION.»")
    return chr(10).join(L)


async def del_dia(simbolos: list[str] | None = None) -> str:
    """La rutina de los cinco activos, para correr antes de Londres."""
    simbolos = simbolos or list(D.ACTIVOS)
    datos = await D.cargar(simbolos)
    from zoneinfo import ZoneInfo
    ahora = datetime.now(ZoneInfo("America/New_York"))
    partes = []
    for s in simbolos:
        if s in datos:
            partes.append(texto(analizar(s, datos[s], ahora)))
    return (chr(10) * 2 + "─" * 68 + chr(10) * 2).join(partes)


if __name__ == "__main__":
    import asyncio
    print(asyncio.run(del_dia()))
