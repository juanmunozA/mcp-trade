"""
La autopsia: `python -m cripto.analizar`

POR QUE ESTO VIVE EN EL REPO
────────────────────────────
El entrenamiento de este agente no sale de leer teoria: sale de medir sus
propias operaciones resueltas y escribir lo medido en `cripto/notas/`. Si el
analisis no esta versionado, nadie mas puede repetirlo ni discutirlo, y las
notas se vuelven afirmaciones sin respaldo.

Cada bloque imprime el numero Y su intervalo. Un acierto sin intervalo no
sirve para decidir nada.

EL AGRUPAMIENTO NO ES OPCIONAL
Los pares se predicen en el mismo instante y se mueven juntos, asi que las
filas no son independientes. Todo intervalo va corregido por el efecto de
diseno; sin eso cualquier regla parece un descubrimiento.
"""

from __future__ import annotations

import asyncio
import collections
import json
import math
import os

import asyncpg
import numpy as np
from dotenv import load_dotenv

load_dotenv()

COSTO = float(os.getenv("CRIPTO_COSTO_PCT", "0.10"))


async def traer() -> list[dict]:
    """Todo lo resuelto, con el contexto de mercado que vio el modelo."""
    dsn = os.environ["DATABASE_URL"].replace("-pooler", "")
    c = await asyncpg.connect(dsn)
    try:
        filas = await c.fetch(
            "SELECT ts, simbolo, precio_ini, precio_fin, p_agente, p_inercia,"
            " subio, fundamento, mov_esperado, contexto FROM cripto.predicciones"
            " WHERE subio IS NOT NULL AND precio_ini > 0 ORDER BY ts, simbolo")
    finally:
        await c.close()
    out = []
    for f in filas:
        d = dict(f)
        d["ts"] = str(d["ts"])
        d["contexto"] = json.loads(d["contexto"]) if d["contexto"] else {}
        out.append(d)
    return out


def var(x: dict, k: str, dv: float = 0.0) -> float:
    v = (x.get("contexto") or {}).get(k)
    return dv if v is None else float(v)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de Wilson: no se rompe con n chico ni con proporciones cerca de 0 o 1."""
    if not n:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def efecto_diseno(y: np.ndarray, ciclo: np.ndarray) -> tuple[float, float]:
    """
    Cuantas observaciones valen de verdad.

    Con ocho pares correlacionados dentro del mismo ciclo, `n` filas no son
    `n` datos. Devuelve (rho, deff): los intervalos se ensanchan sqrt(deff).
    """
    gr = collections.defaultdict(list)
    for i, c in enumerate(ciclo):
        gr[c].append(y[i])
    ob = float(y.mean())
    if ob in (0.0, 1.0):
        return 0.0, 1.0
    num = den = 0.0
    tot = ngr = 0
    for v in gr.values():
        k = len(v)
        if k < 2:
            continue
        a = np.array(v)
        tot += k
        ngr += 1
        num += (a - ob).sum() ** 2 - ((a - ob) ** 2).sum()
        den += k * (k - 1)
    if not den or not ngr:
        return 0.0, 1.0
    rho = (num / den) / (ob * (1 - ob))
    return rho, max(1.0, 1 + (tot / ngr - 1) * rho)


def murphy(p: np.ndarray, y: np.ndarray) -> dict:
    """
    Brier = fiabilidad - resolucion + incertidumbre.

    Separa dos enfermedades que el Brier solo confunde: decir 0.60 cuando
    pasa el 45% (calibracion) se arregla moviendo numeros; no separar nunca
    los casos (resolucion cerca de cero) no se arregla asi.
    """
    ob = float(y.mean())
    k = np.clip(np.digitize(p, np.linspace(0, 1, 11)) - 1, 0, 9)
    rel = res = 0.0
    for j in range(10):
        m = k == j
        if not m.sum():
            continue
        rel += m.sum() * (p[m].mean() - y[m].mean()) ** 2
        res += m.sum() * (y[m].mean() - ob) ** 2
    n = len(y)
    return {"brier": float(((p - y) ** 2).mean()), "fiabilidad": rel / n,
            "resolucion": res / n, "incertidumbre": ob * (1 - ob)}


def logistica(X: np.ndarray, y: np.ndarray, ciclo: np.ndarray):
    """IRLS con errores agrupados por ciclo (sandwich de Huber-White)."""
    b = np.zeros(X.shape[1])
    for _ in range(60):
        pr = 1 / (1 + np.exp(-X @ b))
        W = np.clip(pr * (1 - pr), 1e-9, None)
        H = X.T @ (X * W[:, None]) + 1e-6 * np.eye(len(b))
        b = b + np.linalg.solve(H, X.T @ (y - pr))
    pr = 1 / (1 + np.exp(-X @ b))
    Hin = np.linalg.inv(X.T @ (X * np.clip(pr * (1 - pr), 1e-9, None)[:, None])
                        + 1e-6 * np.eye(len(b)))
    M = np.zeros((len(b), len(b)))
    for c in set(ciclo):
        m = ciclo == c
        s = (X[m] * ((y - pr)[m])[:, None]).sum(0)
        M += np.outer(s, s)
    k = len(set(ciclo))
    return b, np.sqrt(np.diag(Hin @ M @ Hin * (k / max(1, k - 1))))


VARIABLES = [("rsi_5m", 50.0), ("cambio_1h", 0.0), ("flujo_agresor", 0.0),
             ("libro_desequilibrio", 0.0), ("volumen_vs_medio", 1.0),
             ("pos_en_rango_24h", 0.5)]

REGLAS = [
    ("agente (lo que hizo)", lambda x: x["p_agente"] > 0.5),
    ("RSI5m <40 compra / >60 vende",
     lambda x: True if var(x, "rsi_5m", 50) < 40
     else (False if var(x, "rsi_5m", 50) > 60 else None)),
    ("RSI5m <50 compra / >50 vende", lambda x: var(x, "rsi_5m", 50) < 50),
    ("contrario a cambio_1h", lambda x: var(x, "cambio_1h") < 0),
    ("contrario a ema9_vs_ema21", lambda x: var(x, "ema9_vs_ema21") < 0),
    ("seguir flujo_agresor", lambda x: var(x, "flujo_agresor") > 0),
]


def informe(d: list[dict]) -> str:
    L: list[str] = []

    def t(s: str = "") -> None:
        L.append(s)

    y = np.array([1.0 if x["subio"] else 0.0 for x in d])
    p = np.array([float(x["p_agente"]) for x in d])
    pin = np.array([float(x["p_inercia"]) for x in d])
    r = np.array([(x["precio_fin"] / x["precio_ini"] - 1) * 100 for x in d])
    cl = np.array([x["ts"][:19] for x in d])
    N = len(d)
    acierto = float(((p > 0.5) == (y > 0.5)).mean())

    rho, deff = efecto_diseno(y, cl)
    ens = math.sqrt(deff)

    t(f"n = {N} filas | {len(set(cl))} ciclos | "
      f"{len(set(x['simbolo'] for x in d))} pares")
    t(f"acierto {100 * acierto:.1f}% | Brier agente {((p - y) ** 2).mean():.4f} "
      f"azar {((0.5 - y) ** 2).mean():.4f} inercia {((pin - y) ** 2).mean():.4f}")

    t()
    t("1. EL n EFECTIVO")
    t(f"   correlacion intra-ciclo rho = {rho:.3f} -> efecto de diseno {deff:.2f}")
    t(f"   n efectivo = {N / deff:.0f}. Los intervalos se ensanchan {ens:.2f}x.")

    m = murphy(p, y)
    t()
    t("2. DESCOMPOSICION DE MURPHY")
    t(f"   Brier {m['brier']:.4f} = fiabilidad {m['fiabilidad']:.4f}"
      f" - resolucion {m['resolucion']:.4f}"
      f" + incertidumbre {m['incertidumbre']:.4f}")
    t("   fiabilidad alta = mal calibrado, se arregla. resolucion ~0 = no separa casos.")

    med = float(np.median(np.abs(r)))
    pest = 0.5 + COSTO / (2 * med)
    t()
    t("3. EL UMBRAL DE RENTABILIDAD")
    t("   EV = m(2p-1) - c   |   p* = 0.5 + c/(2m)")
    t(f"   m mediano = {med:.3f}%, c = {COSTO}%  ->  p* = {100 * pest:.1f}%")
    t(f"   acierto real {100 * acierto:.1f}% -> faltan "
      f"{100 * pest - 100 * acierto:.1f} puntos")
    t("   cuantiles |mov|: " + "  ".join(
        f"p{q}={np.percentile(np.abs(r), q):.3f}%" for q in (25, 50, 75, 90)))

    t()
    t("4. REGLAS, CON INTERVALO AGRUPADO")
    t(f"   {'regla':32}{'n':>5}{'acierto':>9}{'IC 95%':>17}{'neto':>10}")
    for nom, lado in REGLAS:
        idx = [i for i in range(N) if lado(d[i]) is not None]
        if len(idx) < 8:
            continue
        ok = np.array([1.0 if lado(d[i]) == (y[i] > 0.5) else 0.0 for i in idx])
        n, ph = len(idx), float(ok.mean())
        se = math.sqrt(ph * (1 - ph) / n) * ens
        pnl = sum((r[i] if lado(d[i]) else -r[i]) - COSTO for i in idx)
        t(f"   {nom:32}{n:5}{100 * ph:8.1f}%"
          f"  [{100 * (ph - 1.96 * se):5.1f},{100 * (ph + 1.96 * se):6.1f}]"
          f"{pnl:+9.2f}%")
    t(f"   el umbral que importa es p* = {100 * pest:.1f}%, no 50%.")
    t(f"   con {len(REGLAS)} reglas probadas Bonferroni pide z > "
      f"{2.86 if len(REGLAS) > 8 else 2.64:.2f}, no 1.96.")

    X = np.column_stack([np.ones(N)] + [
        np.array([var(x, k, dv) for x in d]) for k, dv in VARIABLES])
    sd = np.where(X[:, 1:].std(0) == 0, 1.0, X[:, 1:].std(0))
    Xs = np.column_stack([np.ones(N), (X[:, 1:] - X[:, 1:].mean(0)) / sd])
    b, se = logistica(Xs, y, cl)
    t()
    t("5. REGRESION LOGISTICA (estandarizada, errores agrupados)")
    for i, nm in enumerate(["(intercepto)"] + [k for k, _ in VARIABLES]):
        z = b[i] / se[i] if se[i] else 0.0
        t(f"   {nm:22}{b[i]:+8.3f}  z={z:+6.2f}  odds x1sd {math.exp(b[i]):5.2f}"
          f"{'  *' if abs(z) > 1.96 else ''}")

    oos = np.full(N, 0.5)
    for c in sorted(set(cl)):
        te = cl == c
        tr = ~te
        if tr.sum() < 20 or len(set(y[tr])) < 2:
            continue
        bb, _ = logistica(Xs[tr], y[tr], cl[tr])
        oos[te] = 1 / (1 + np.exp(-Xs[te] @ bb))
    pnl = sum((r[i] if oos[i] > 0.5 else -r[i]) - COSTO for i in range(N))
    t()
    t("6. FUERA DE MUESTRA (dejar-un-ciclo-afuera)")
    t(f"   acierto {100 * ((oos > 0.5) == (y > 0.5)).mean():.1f}%  "
      f"Brier {((oos - y) ** 2).mean():.4f}  neto {pnl:+.2f}%")
    t(f"   solo vale si supera {100 * pest:.1f}%. Dentro de muestra siempre miente.")

    rsi = np.array([var(x, "rsi_5m", 50) for x in d])
    h1 = np.array([var(x, "cambio_1h") for x in d])
    t()
    t("7. TABLA CONDICIONAL rsi_5m x cambio_1h  (P de que suba)")
    t(f"   {'':14}{'1h bajando':>24}{'1h subiendo':>24}")
    for lo, hi, lab in [(0, 40, "rsi<40"), (40, 50, "rsi 40-50"),
                        (50, 60, "rsi 50-60"), (60, 75, "rsi 60-75"),
                        (75, 101, "rsi>75")]:
        cel = []
        for cond in (h1 < 0, h1 >= 0):
            mk = (rsi >= lo) & (rsi < hi) & cond
            n = int(mk.sum())
            if n < 5:
                cel.append(f"{'n<5':>24}")
                continue
            k = int(y[mk].sum())
            a, bq = wilson(k, n, 1.96 * ens)
            cel.append(f"{k / n:6.2f} [{max(0, a):.2f},{min(1, bq):.2f}] n={n:3}")
        t(f"   {lab:14}{cel[0]:>24}{cel[1]:>24}")

    g = collections.defaultdict(list)
    for i, x in enumerate(d):
        if x["fundamento"]:
            g[x["fundamento"]].append(i)
    if g:
        t()
        t("8. POR FUNDAMENTO DECLARADO")
        for k, idx in sorted(g.items(), key=lambda a: -len(a[1])):
            ac = sum(1 for i in idx if (p[i] > 0.5) == (y[i] > 0.5))
            a, bq = wilson(ac, len(idx), 1.96 * ens)
            neto = sum((r[i] if p[i] > 0.5 else -r[i]) - COSTO for i in idx)
            t(f"   {k:26} n={len(idx):3}  acierto {100 * ac / len(idx):5.1f}% "
              f"[{100 * max(0, a):4.1f},{100 * min(1, bq):5.1f}]  neto {neto:+7.2f}%")

    esp = np.array([abs(float(x["mov_esperado"] or 0)) for x in d])
    v = esp > 0
    if v.sum() > 10:
        t()
        t("9. CALIBRACION DEL TAMANO")
        t(f"   dijo {esp[v].mean():.3f}%  |  real mediano {med:.3f}%  "
          f"->  se queda corto {med / max(1e-9, esp[v].mean()):.2f}x")

    t()
    t("Ninguna cifra de aqui autoriza conviccion por si sola. Mira el intervalo,")
    t("acordate de cuantas reglas se probaron, y escribi en las notas lo que")
    t("sobrevive -- incluido lo que NO sobrevive, que es la mitad del valor.")
    return chr(10).join(L)


async def main() -> None:
    d = await traer()
    if len(d) < 20:
        print(f"solo {len(d)} operaciones resueltas: muy poco para medir nada")
        return
    print(informe(d))


if __name__ == "__main__":
    asyncio.run(main())
