"""
Entrena las dos redes y las juzga con datos que no vieron.

    direccion   ¿a 1 hora vista el precio esta arriba de la entrada?
    retroceso   ¿cuantos pips va a ir en contra antes de moverse a favor?
                (compra y venta), en log para que los dias locos no manden

Cada una se entrena dos veces: como una sola neurona y como red de dos
capas. Se imprime todo junto, con la linea base al lado, para que se vea
si la red aporta o si solo parece que aporta.
"""

from __future__ import annotations

import numpy as np

from materias.red import datos as D
from materias.red import entradas as F
from materias.red import etiquetas as E
from materias.red.modelo import Red, auc, partir
from materias.red.pips import hora_ny

HORIZONTE = 12          # 12 velas de 5M = 1 hora


def preparar(tf="M5", horizonte=HORIZONTE):
    v = D.cargar(tf)
    X, nombres = F.calcular(v)
    e = E.calcular(v, horizonte)
    n = len(e["ret"])
    X = X[:n]
    ok = ~np.isnan(X).any(axis=1)
    return X[ok], {k: a[ok] for k, a in e.items()}, nombres


def informe(horizonte=HORIZONTE, guardar=True):
    X, e, nombres = preparar(horizonte=horizonte)
    ent, val, pru = partir(len(X), horizonte)
    print(f"{len(X):,} ejemplos · entrenamiento {len(ent):,} · validacion "
          f"{len(val):,} · prueba {len(pru):,}  (cronologico)")

    # ── 1. direccion ─────────────────────────────────────────────────────
    y = (e["ret"] > 0).astype(float)
    print(f"\nDIRECCION a {horizonte*5} min · en prueba sube el {100*y[pru].mean():.1f} %")
    redes = {}
    for nom, capas in (("1 neurona", ()), ("red 32-16", (32, 16))):
        r = Red(capas=capas, tipo="clasificar")
        r.entrenar(X[ent], y[ent], X[val], y[val])
        p = r.predecir(X[pru]).ravel()
        acierto = ((p > 0.5) == (y[pru] == 1)).mean()
        seg = np.abs(p - 0.5) > 0.05
        acierto_seg = ((p[seg] > 0.5) == (y[pru][seg] == 1)).mean() if seg.any() else np.nan
        print(f"  {nom:10} acierto {100*acierto:5.2f} % · AUC {auc(p, y[pru]):.3f} · "
              f"cuando esta segura ({100*seg.mean():4.1f} % de las velas): {100*acierto_seg:5.2f} %")
        redes[nom] = r
    if guardar:
        # Se elige con VALIDACION (elegir con prueba seria hacer trampa) y se
        # guarda con su AUC de PRUEBA, para que el agente vea cuanto vale.
        mejor = max(redes, key=lambda k: auc(redes[k].predecir(X[val]).ravel(), y[val]))
        redes[mejor].guardar(D.CARPETA / "red_direccion.npz")
        import json
        (D.CARPETA / "metricas.json").write_text(json.dumps({"direccion": {
            "modelo": mejor, "horizonte_min": horizonte * 5,
            "auc": auc(redes[mejor].predecir(X[pru]).ravel(), y[pru]),
            "n_prueba": int(len(pru))}}, indent=1), encoding="utf-8")

    # ── 2. retroceso previo, en pips ────────────────────────────────────
    print("\nRETROCESO antes de moverse a favor (pips) · error medio absoluto en prueba")
    Y = np.log1p(np.column_stack([e["largo_mae_previa"], e["corto_mae_previa"]]))
    real = np.expm1(Y[pru])
    # linea base: la mediana de su sesion en el entrenamiento
    hny_ent = hora_ny(e["tiempo"][ent]).astype(int)
    hny_pru = hora_ny(e["tiempo"][pru]).astype(int)
    med = np.array([[np.median(np.expm1(Y[ent][hny_ent == hh, j])) for j in (0, 1)]
                    for hh in range(24)])
    base = med[hny_pru]
    print(f"  {'mediana por hora':18} compra {np.abs(base[:,0]-real[:,0]).mean():5.1f} · "
          f"venta {np.abs(base[:,1]-real[:,1]).mean():5.1f}")
    for nom, capas in (("1 neurona", ()), ("red 32-16", (32, 16))):
        r = Red(capas=capas, salidas=2, tipo="regresion")
        r.entrenar(X[ent], Y[ent], X[val], Y[val])
        pr = np.expm1(r.predecir(X[pru]))
        print(f"  {nom:18} compra {np.abs(pr[:,0]-real[:,0]).mean():5.1f} · "
              f"venta {np.abs(pr[:,1]-real[:,1]).mean():5.1f}")
        if guardar and capas:
            r.guardar(D.CARPETA / "red_retroceso.npz")
    np.save(D.CARPETA / "nombres_entradas.npy", np.array(nombres))


if __name__ == "__main__":
    informe()
