"""
La red: neuronas que multiplican y suman, con pesos que salen de los datos.

Exactamente lo del video del paraguas, pero con 27 entradas en vez de 3:
cada neurona multiplica cada entrada por un peso, suma, y pasa el
resultado por una curva. Los pesos empiezan al azar y el entrenamiento los
corrige poco a poco hasta que la red acierta mas.

DOS TAMANOS, Y EL GRANDE TIENE QUE GANARSE EL PUESTO
- `capas=()`  → una sola neurona: la regresion logistica. Es la linea base.
- `capas=(32, 16)` → dos capas ocultas. Solo vale si le gana a la sola
  neurona con datos que ninguna de las dos vio.

POR QUE SE PARTE EN EL TIEMPO Y NO AL AZAR
Si se mezclan las velas, la red ve el martes para predecir el lunes y
parece adivina. Se entrena con lo viejo, se elige el punto de parada con
lo siguiente y se juzga con lo mas nuevo, dejando un hueco entre medias
del tamano del horizonte para que ninguna etiqueta se solape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Red:
    capas: tuple[int, ...] = (32, 16)
    salidas: int = 1
    tipo: str = "clasificar"           # "clasificar" (sigmoide) o "regresion"
    semilla: int = 7
    W: list = field(default_factory=list)
    b: list = field(default_factory=list)
    media: np.ndarray | None = None
    desv: np.ndarray | None = None

    # ── la parte que de verdad es la red ────────────────────────────────
    def _iniciar(self, entradas: int):
        rng = np.random.default_rng(self.semilla)
        tam = [entradas, *self.capas, self.salidas]
        self.W = [rng.normal(0, np.sqrt(2 / a), (a, b_)) for a, b_ in zip(tam, tam[1:], strict=False)]
        self.b = [np.zeros(b_) for b_ in tam[1:]]

    def _adelante(self, X):
        activ = [X]
        for k, (W, b) in enumerate(zip(self.W, self.b, strict=True)):
            z = activ[-1] @ W + b                    # multiplicar y sumar
            ultima = k == len(self.W) - 1
            if not ultima:
                z = np.maximum(z, 0.0)               # la «curva» (ReLU)
            elif self.tipo == "clasificar":
                z = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
            activ.append(z)
        return activ

    def predecir(self, X):
        return self._adelante((X - self.media) / self.desv)[-1]

    # ── entrenamiento ───────────────────────────────────────────────────
    def entrenar(self, X, y, Xv, yv, epocas=60, lote=512, lr=1e-3,
                 l2=1e-4, paciencia=6, verbose=False):
        self.media = np.nanmean(X, axis=0)
        self.desv = np.nanstd(X, axis=0) + 1e-9
        X = (X - self.media) / self.desv
        Xv_n = (Xv - self.media) / self.desv
        y = y.reshape(len(y), -1)
        yv = yv.reshape(len(yv), -1)
        self._iniciar(X.shape[1])
        m = [np.zeros_like(p) for p in self.W + self.b]
        v = [np.zeros_like(p) for p in self.W + self.b]
        paso, mejor, sin_mejora, mejores = 0, np.inf, 0, None
        rng = np.random.default_rng(self.semilla)
        for ep in range(epocas):
            orden = rng.permutation(len(X))
            for i in range(0, len(X), lote):
                idx = orden[i:i + lote]
                activ = self._adelante(X[idx])
                # derivada de la perdida respecto a la salida: igual para
                # entropia cruzada+sigmoide y para error cuadratico
                d = (activ[-1] - y[idx]) / len(idx)
                grads_W, grads_b = [], []
                for k in range(len(self.W) - 1, -1, -1):
                    grads_W.insert(0, activ[k].T @ d + l2 * self.W[k])
                    grads_b.insert(0, d.sum(axis=0))
                    if k:
                        d = (d @ self.W[k].T) * (activ[k] > 0)
                paso += 1
                for j, (p, g) in enumerate(zip(self.W + self.b, grads_W + grads_b, strict=True)):
                    m[j] = 0.9 * m[j] + 0.1 * g
                    v[j] = 0.999 * v[j] + 0.001 * g * g
                    mh = m[j] / (1 - 0.9 ** paso)
                    vh = v[j] / (1 - 0.999 ** paso)
                    p -= lr * mh / (np.sqrt(vh) + 1e-8)
            pv = self._adelante(Xv_n)[-1]
            perdida = _perdida(pv, yv, self.tipo)
            if verbose:
                print(f"  epoca {ep:2d} · perdida validacion {perdida:.5f}")
            if perdida < mejor - 1e-5:
                mejor, sin_mejora = perdida, 0
                mejores = ([w.copy() for w in self.W], [b_.copy() for b_ in self.b])
            else:
                sin_mejora += 1
                if sin_mejora >= paciencia:
                    break
        self.W, self.b = mejores
        return mejor

    def guardar(self, ruta):
        np.savez(ruta, capas=np.array(self.capas), salidas=self.salidas,
                 tipo=self.tipo, media=self.media, desv=self.desv,
                 **{f"W{k}": w for k, w in enumerate(self.W)},
                 **{f"b{k}": b_ for k, b_ in enumerate(self.b)})

    @classmethod
    def cargar(cls, ruta):
        d = np.load(ruta, allow_pickle=False)
        r = cls(capas=tuple(int(x) for x in d["capas"]), salidas=int(d["salidas"]),
                tipo=str(d["tipo"]))
        r.media, r.desv = d["media"], d["desv"]
        n = len([k for k in d.files if k.startswith("W")])
        r.W = [d[f"W{k}"] for k in range(n)]
        r.b = [d[f"b{k}"] for k in range(n)]
        return r


def _perdida(p, y, tipo):
    if tipo == "clasificar":
        p = np.clip(p, 1e-7, 1 - 1e-7)
        return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())
    return float(((p - y) ** 2).mean())


def partir(n: int, hueco: int, ent=0.70, val=0.15):
    """Indices cronologicos de entrenamiento, validacion y prueba."""
    a = int(n * ent)
    b = int(n * (ent + val))
    return (np.arange(0, a - hueco), np.arange(a, b - hueco), np.arange(b, n))


def auc(p, y):
    """Probabilidad de que una vela que subio tenga mas nota que una que bajo."""
    orden = np.argsort(p)
    r = np.empty(len(p))
    r[orden] = np.arange(1, len(p) + 1)
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))
