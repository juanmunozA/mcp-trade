"""
La probabilidad estadistica, calculada en codigo y no de cabeza.

POR QUE EXISTE ESTE ARCHIVO
───────────────────────────
Durante la primera tanda al modelo se le daban las tablas medidas dentro
del prompt y se esperaba que hiciera la consulta y la aritmetica solo.
Medido sobre las mismas 261 operaciones:

    regresion fuera de muestra   54,4% de acierto   neto +17,02%
    el LLM leyendo las tablas    47,1%              neto -62,15%

La regresion gana, y no porque sea lista: gana porque una cuenta
aritmetica la hace mejor una cuenta que un lector. Pedirle a un modelo de
lenguaje que interpole una tabla de contingencia es usarlo justo para lo
que peor hace.

Asi que el reparto de trabajo cambia. Aca se calcula `p_base` con los
datos historicos; el modelo la recibe como insumo y solo puede moverla
dentro de un margen, justificando por que. Y las dos quedan guardadas,
que es lo unico que permite responder despues si el modelo aporta algo o
si conviene sacarlo del circuito.

CONTRA EL SOBREAJUSTE, TRES FRENOS
    - penalizacion ridge, porque con ~30 ciclos los coeficientes se
      disparan solos
    - contraccion hacia 0.50 segun cuantos datos hay
    - un piso de operaciones resueltas: por debajo devuelve None y el
      sistema sigue sin ella en vez de inventar
"""

from __future__ import annotations

import collections
import json
import logging
import math

import numpy as np

log = logging.getLogger("cripto.modelo")

MINIMO = 80
"""
Operaciones resueltas antes de creerle una sola cifra.

Con menos, los coeficientes son ruido con signo. Por debajo de esto
`ajustar` devuelve None y el modelo de lenguaje decide solo, que es peor
pero al menos no viene disfrazado de estadistica.
"""

RIDGE = 2.0
"""Penalizacion. Alta a proposito: importa mas no delirar que ajustar fino."""

TOPE = 0.18
"""
Cuanto puede alejarse `p_base` de 0.50.

Con n efectivo cerca de 90 y una sola sesion medida, un 0.30 no esta
respaldado por nada. El tope es del estimador, no del mercado.
"""

CAMPOS = ["rsi_5m", "rsi_relativo", "cambio_1h", "flujo_agresor",
          "volumen_vs_medio"]
PORDEFECTO = {"rsi_5m": 50.0, "rsi_relativo": 0.0, "cambio_1h": 0.0,
              "flujo_agresor": 0.0, "volumen_vs_medio": 1.0}


def _rasgos(ctx: dict, rsi_medio: float) -> list[float]:
    """
    Las cinco variables, con `rsi_relativo` incluida a proposito.

    El efecto del RSI se parte en dos: el promedio del ciclo predice la
    direccion del mercado (corr -0,538) y el relativo predice cual par le
    gana a los otros (corr -0,244). Son cosas distintas y el modelo
    necesita las dos separadas para poder usarlas distinto.
    """
    v = []
    for k in CAMPOS:
        if k == "rsi_relativo":
            v.append(float(ctx.get("rsi_5m", 50.0)) - rsi_medio)
        else:
            x = ctx.get(k)
            v.append(PORDEFECTO[k] if x is None else float(x))
    return v


def _irls(X: np.ndarray, y: np.ndarray, ridge: float) -> np.ndarray:
    """Logistica por minimos cuadrados reponderados, con ridge salvo el intercepto."""
    b = np.zeros(X.shape[1])
    P = np.eye(X.shape[1]) * ridge
    P[0, 0] = 0.0
    for _ in range(50):
        pr = 1 / (1 + np.exp(-np.clip(X @ b, -30, 30)))
        W = np.clip(pr * (1 - pr), 1e-6, None)
        H = X.T @ (X * W[:, None]) + P
        paso = np.linalg.solve(H, X.T @ (y - pr) - P @ b)
        b = b + paso
        if np.abs(paso).max() < 1e-8:
            break
    return b


class Modelo:
    """
    Se reajusta con cada tanda nueva y da una probabilidad por par.

    No guarda estado entre corridas: cada arranque vuelve a aprender de
    la base. Es barato -- son cientos de filas, no millones -- y evita que
    un modelo viejo sobreviva a un cambio de pares o de regimen sin que
    nadie se entere.
    """

    def __init__(self) -> None:
        self.b: np.ndarray | None = None
        self.mu: np.ndarray | None = None
        self.sd: np.ndarray | None = None
        self.n = 0
        self.contraccion = 0.0

    async def aprender(self, pool) -> bool:
        filas = await pool.fetch(
            "SELECT ts, contexto, subio FROM cripto.predicciones "
            "WHERE subio IS NOT NULL AND contexto IS NOT NULL "
            "ORDER BY ts DESC LIMIT 4000")
        if len(filas) < MINIMO:
            self.b = None
            self.n = len(filas)
            log.info("modelo: %d resueltas, hacen falta %d", len(filas), MINIMO)
            return False

        porciclo = collections.defaultdict(list)
        for f in filas:
            porciclo[str(f["ts"])[:19]].append(f)

        X, y = [], []
        for _, grupo in porciclo.items():
            ctxs = [json.loads(g["contexto"]) if isinstance(g["contexto"], str)
                    else g["contexto"] for g in grupo]
            rsis = [float(c.get("rsi_5m", 50.0)) for c in ctxs]
            medio = sum(rsis) / len(rsis)
            for c, g in zip(ctxs, grupo, strict=False):
                X.append(_rasgos(c, medio))
                y.append(1.0 if g["subio"] else 0.0)
        X = np.array(X, dtype=float)
        y = np.array(y, dtype=float)
        if len(set(y)) < 2:
            self.b = None
            return False

        self.mu = X.mean(0)
        self.sd = np.where(X.std(0) == 0, 1.0, X.std(0))
        Z = np.column_stack([np.ones(len(X)), (X - self.mu) / self.sd])
        self.b = _irls(Z, y, RIDGE)
        self.n = len(X)
        # Cuanto se le cree al estimador. Con pocos datos casi nada; el
        # peso se acerca a 1 solo cuando hay varios miles de filas.
        self.contraccion = self.n / (self.n + 400)
        log.info("modelo: %d filas, %d ciclos, contraccion %.2f | %s",
                 self.n, len(porciclo), self.contraccion,
                 ", ".join(f"{k}={v:+.2f}" for k, v in
                           zip(CAMPOS, self.b[1:], strict=False)))
        return True

    def p_base(self, fotos: list[dict]) -> dict[str, float] | None:
        """
        Una probabilidad por simbolo, o None si todavia no hay con que.

        El `rsi_relativo` se calcula contra esta misma tanda, que es como
        se entreno: el par se compara con sus hermanos del mismo instante,
        no con una media historica.
        """
        if self.b is None or not fotos:
            return None
        rsis = [float(f.get("rsi_5m", 50.0)) for f in fotos]
        medio = sum(rsis) / len(rsis)
        X = np.array([_rasgos(f, medio) for f in fotos], dtype=float)
        Z = np.column_stack([np.ones(len(X)), (X - self.mu) / self.sd])
        p = 1 / (1 + np.exp(-np.clip(Z @ self.b, -30, 30)))
        # Contraer y despues recortar. El orden importa: recortar primero
        # dejaria pasar como "tope" una conviccion que el tamano de
        # muestra no sostiene.
        p = 0.5 + (p - 0.5) * self.contraccion
        p = np.clip(p, 0.5 - TOPE, 0.5 + TOPE)
        return {f["simbolo"]: round(float(v), 4)
                for f, v in zip(fotos, p, strict=False)}

    def resumen(self) -> str:
        """Para el prompt: que aprendio y con cuanto respaldo."""
        if self.b is None:
            return ""
        pesos = ", ".join(f"{k} {v:+.2f}" for k, v in
                          zip(CAMPOS, self.b[1:], strict=False))
        return (f"ajustada sobre {self.n} operaciones resueltas, "
                f"contraida al {self.contraccion:.0%} | pesos: {pesos}")
