"""
La red grande: un Transformer que lee las velas en bruto de cuatro
temporalidades y aprende solo que mirar.

Es la escalera del segundo video, en oro:
    cada vela        -> una lista de numeros (embedding)
    todas las velas  -> bloques de atencion: cada vela «pregunta» a las
                        demas cuanto le importan, y la red aprende que
                        una barrida de hace 40 minutos pesa mas que el
                        ruido de hace dos horas
    el resumen       -> varias respuestas a la vez

QUE VE (todo relativo al precio y al ATR del momento de decidir, para que
2019 a 1.300 y 2026 a 4.100 hablen el mismo idioma):
    48 velas de 5M · 32 de 15M · 32 de 1H · 24 de 4H, solo las ya CERRADAS
    hora de Nueva York, dia de la semana y killzone

QUE RESPONDE (siete salidas, una sola red):
    sube_1h, sube_4h            ¿precio mas alto dentro de 1 / 4 horas?
    toque_largo, toque_corto    ¿toca antes +3 ATR que -3 ATR? Es la pregunta
                                de un trade de verdad: objetivo y stop iguales
    retroceso_largo, _corto     pips en contra antes del extremo a favor
    rango_1h                    pips entre maximo y minimo de la hora

COMO SE JUZGA
Igual que la pequena: particion cronologica 70/15/15 con hueco, y contra
las lineas base que ya existen (la tabla por hora, la moneda al aire, la
tasa base del toque). Si no las supera con datos que no vio, no se usa.

LO QUE NO VE
Noticias. No hay un historico de titulares 2019-2026 alineado a cada
vela, y fabricarlo seria hacer trampa. Las noticias las lee el agente en
vivo y razona con ellas junto a lo que diga esta red.
"""

from __future__ import annotations

import json
import time

import numpy as np

from materias.red import datos as D
from materias.red import etiquetas as E
from materias.red.entradas import _atr
from materias.red.pips import SESIONES, en_sesion, hora_ny

VENTANAS = {"5m": (5, 24), "15m": (15, 16), "1h": (60, 16), "4h": (240, 12)}
"""
Cuantas velas de cada temporalidad ve la red.

Eran 48/32/32/24 (136 fichas) y en el i5-1235U de este portatil un paso de
256 ejemplos tardaba 3 s: diez horas sin terminar la primera tanda. La
atencion cuesta el cuadrado del numero de fichas, asi que la mitad de
velas es una cuarta parte del coste, y lo que se pierde es lo mas viejo
de cada marco, que es tambien lo que menos pesa.
"""
K_ATR = 3.0            # objetivo y stop del «toque», en ATR de 5M
H1, H4 = 12, 48        # horizontes en velas de 5M
SALIDAS = ["sube_1h", "sube_4h", "toque_largo", "toque_corto",
           "retroceso_largo", "retroceso_corto", "rango_1h"]
CLASIF = {"sube_1h", "sube_4h", "toque_largo", "toque_corto"}


# ══════════════════════════════════════════════════════════════════════
# Datos
# ══════════════════════════════════════════════════════════════════════

def _agregar(v: np.ndarray, minutos: int):
    """Velas de 5M agrupadas en velas de `minutos` (por hora del servidor)."""
    cubo = v["time"].astype(np.int64) // (minutos * 60)
    corte = np.flatnonzero(np.diff(cubo)) + 1
    ini = np.concatenate([[0], corte])
    fin = np.concatenate([corte, [len(v)]])
    o = v["open"][ini]
    c = v["close"][fin - 1]
    h = np.maximum.reduceat(v["high"], ini)
    lo = np.minimum.reduceat(v["low"], ini)
    return np.column_stack([o, h, lo, c]).astype(np.float32), cubo[ini]


def primer_toque(v: np.ndarray, horizonte: int, dist: np.ndarray) -> np.ndarray:
    """+1 si toca antes +dist que -dist (desde la apertura siguiente), -1 al reves, 0 ninguno."""
    from numpy.lib.stride_tricks import sliding_window_view as ventanas
    n = len(v) - horizonte
    ent = v["open"][1:n + 1].astype(float)
    Hs = ventanas(v["high"][1:].astype(float), horizonte)[:n]
    Ls = ventanas(v["low"][1:].astype(float), horizonte)[:n]
    d = dist[:n, None]
    sube = Hs >= ent[:, None] + d
    baja = Ls <= ent[:, None] - d
    t_s = np.where(sube.any(1), sube.argmax(1), horizonte + 1)
    t_b = np.where(baja.any(1), baja.argmax(1), horizonte + 1)
    # misma vela toca los dos: no se sabe el orden; se cuenta como stop (lo prudente)
    return np.where(t_s < t_b, 1, np.where(t_b < t_s, -1, np.where(t_s <= horizonte, -1, 0)))


def preparar():
    v = D.cargar("M5")
    n = len(v)
    o = v["open"].astype(np.float32)
    c = v["close"].astype(np.float32)
    atr = _atr(v["high"].astype(float), v["low"].astype(float), c.astype(float)).astype(np.float32)
    marcos, indices = {}, {}
    cubo5 = v["time"].astype(np.int64)
    for k, (minutos, _) in VENTANAS.items():
        if minutos == 5:
            ohlc = np.column_stack([o, v["high"], v["low"], c]).astype(np.float32)
            # Se decide al cierre de la vela i (la entrada es la apertura de
            # i+1, ver etiquetas), asi que la vela i ya cuenta como cerrada.
            idx = np.arange(n)
        else:
            ohlc, cubos = _agregar(v, minutos)
            cubo_i = cubo5 // (minutos * 60)
            idx = np.searchsorted(cubos, cubo_i, side="left") - 1   # ultima vela del marco ya cerrada
        marcos[k] = ohlc
        indices[k] = idx
    hny = hora_ny(v["time"])
    dia = ((v["time"].astype(np.int64) - 7 * 3600) // 86400 + 4) % 7
    ctx = [np.sin(2 * np.pi * hny / 24), np.cos(2 * np.pi * hny / 24),
           np.sin(2 * np.pi * dia / 7), np.cos(2 * np.pi * dia / 7)]
    ctx += [en_sesion(hny, a, b).astype(float) for a, b in SESIONES.values()]
    contexto = np.column_stack(ctx).astype(np.float32)

    # etiquetas
    e1 = E.calcular(v, H1)
    e4 = E.calcular(v, H4)
    m = len(e4["ret"])
    dist = K_ATR * atr.astype(float)
    tl = primer_toque(v, H1, dist)[:m]
    y = np.column_stack([
        (e1["ret"][:m] > 0), (e4["ret"] > 0), (tl == 1), (tl == -1),
        np.log1p(e1["largo_mae_previa"][:m]), np.log1p(e1["corto_mae_previa"][:m]),
        np.log1p((e1["largo_mfe"] + e1["largo_mae"])[:m]),
    ]).astype(np.float32)
    # toque_corto: la venta gana si toca antes -dist que +dist, que es tl == -1
    valido_toque = (tl != 0)
    # primeras velas sin historia suficiente en 4H
    ini = int(max(np.searchsorted(indices["4h"], VENTANAS["4h"][1] + 1), 300))
    filas = np.arange(ini, m)
    return {"v": v, "marcos": marcos, "indices": indices, "contexto": contexto,
            "atr": atr, "close": c, "y": y, "valido_toque": valido_toque,
            "filas": filas, "hny": hny}


# ══════════════════════════════════════════════════════════════════════
# La red
# ══════════════════════════════════════════════════════════════════════

def construir(d: int = 32, capas: int = 2, cabezas: int = 4):
    import torch
    from torch import nn

    class Red(nn.Module):
        def __init__(self):
            super().__init__()
            self.emb = nn.ModuleDict({k: nn.Linear(5, d) for k in VENTANAS})
            self.pos = nn.ParameterDict({k: nn.Parameter(torch.zeros(L, d))
                                         for k, (_, L) in VENTANAS.items()})
            self.tipo = nn.ParameterDict({k: nn.Parameter(torch.randn(d) * 0.02) for k in VENTANAS})
            self.cls = nn.Linear(9, d)
            capa = nn.TransformerEncoderLayer(d, cabezas, dim_feedforward=4 * d, dropout=0.1,
                                              batch_first=True, norm_first=True)
            self.enc = nn.TransformerEncoder(capa, capas)
            self.norma = nn.LayerNorm(d)
            self.salida = nn.Linear(d, len(SALIDAS))

        def forward(self, seqs: dict, ctx):
            fichas = [self.cls(ctx).unsqueeze(1)]
            for k, x in seqs.items():
                fichas.append(self.emb[k](x) + self.pos[k] + self.tipo[k])
            z = self.enc(torch.cat(fichas, dim=1))
            return self.salida(self.norma(z[:, 0]))

    return Red()


def _lote(P, filas):
    """Las ventanas de cada temporalidad para `filas`, en ATR relativo al cierre actual."""
    c0 = P["close"][filas][:, None, None]
    a0 = P["atr"][filas][:, None, None]
    seqs = {}
    for k, (_, L) in VENTANAS.items():
        ult = P["indices"][k][filas]                          # ultima vela cerrada
        rejilla = ult[:, None] - np.arange(L - 1, -1, -1)[None, :]
        rejilla = np.clip(rejilla, 0, None)
        x = P["marcos"][k][rejilla]                           # (b, L, 4)
        rel = (x - c0) / a0
        rango = (x[..., 1] - x[..., 2])[..., None] / a0
        seqs[k] = np.concatenate([rel, rango], axis=-1).astype(np.float32)
    return seqs, P["contexto"][filas]


LOG = D.CARPETA / "transformer.log"


def _log(msg: str):
    """A un archivo, con flush: la primera tanda se lanzo con la salida
    filtrada por grep, que la retuvo entera hasta el final, y no hubo forma
    de saber en diez horas si avanzaba."""
    print(msg, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + msg + "\n")


def entrenar(epocas: int = 10, lote: int = 256, lr: float = 5e-4, paso_ent: int = 8,
             hilos: int = 4, guardar: bool = True):
    """
    `paso_ent=8`: una de cada ocho velas. Dos velas de 5M seguidas comparten
    55 de sus 60 minutos siguientes; verlas todas no ensena mas, solo hace
    que la red memorice. `hilos=4`: con 10 el paso tardaba el doble (los
    nucleos de eficiencia de este procesador se estorban).
    """
    import torch
    torch.manual_seed(7)
    torch.set_num_threads(hilos)
    P = preparar()
    f = P["filas"]
    n = len(f)
    a, b = int(n * 0.70), int(n * 0.85)
    ent, val, pru = f[:a - H4:paso_ent], f[a:b - H4:3], f[b:]
    LOG.unlink(missing_ok=True)
    _log(f"{n:,} ejemplos · entrenamiento {len(ent):,} (1 de cada {paso_ent}) · "
          f"validacion {len(val):,} · prueba {len(pru):,}")
    red = construir()
    _log(f"parametros: {sum(p.numel() for p in red.parameters()):,}")
    opt = torch.optim.AdamW(red.parameters(), lr=lr, weight_decay=1e-2)
    es_clasif = torch.tensor([s in CLASIF for s in SALIDAS])

    def perdida(salida, y, vt):
        bce = torch.nn.functional.binary_cross_entropy_with_logits(salida, y, reduction="none")
        mse = (salida - y) ** 2
        L = torch.where(es_clasif, bce, mse)
        # el toque sin resultado (ni +3 ni -3 ATR en la hora) no ensena nada;
        # se anula con un peso y no en el sitio, para no romper el gradiente
        peso = torch.ones_like(L)
        peso[:, 2:4] = vt[:, None]
        return (L * peso).mean()

    def evaluar(filas, cada=1):
        red.eval()
        out = []
        with torch.no_grad():
            for i in range(0, len(filas), 2048):
                s, ctx = _lote(P, filas[i:i + 2048:cada])
                out.append(red({k: torch.from_numpy(x) for k, x in s.items()},
                               torch.from_numpy(ctx)).numpy())
        red.train()
        return np.concatenate(out)

    mejor, sin_mejora, mejores = np.inf, 0, None
    rng = np.random.default_rng(7)
    for ep in range(epocas):
        t0 = time.time()
        orden = rng.permutation(ent)
        for i in range(0, len(orden), lote):
            fl = orden[i:i + lote]
            s, ctx = _lote(P, fl)
            y = torch.from_numpy(P["y"][fl])
            vt = torch.from_numpy(P["valido_toque"][fl].astype(np.float32))
            sal = red({k: torch.from_numpy(x) for k, x in s.items()}, torch.from_numpy(ctx))
            loss = perdida(sal, y, vt)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(red.parameters(), 1.0)
            opt.step()
            paso = i // lote
            if paso % 50 == 0:
                _log(f"  epoca {ep} · paso {paso}/{len(orden) // lote} · perdida {float(loss):.4f}")
        pv = evaluar(val)
        lv = float(perdida(torch.from_numpy(pv), torch.from_numpy(P["y"][val]),
                           torch.from_numpy(P["valido_toque"][val].astype(np.float32))))
        _log(f"EPOCA {ep} · perdida validacion {lv:.4f} · {time.time() - t0:.0f} s")
        if lv < mejor - 1e-4:
            mejor, sin_mejora = lv, 0
            mejores = {k: t.clone() for k, t in red.state_dict().items()}
            torch.save(mejores, D.CARPETA / "transformer.pt")     # punto de control
        else:
            sin_mejora += 1
            if sin_mejora >= 2:
                break
    red.load_state_dict(mejores)
    res = juzgar(P, ent, pru, evaluar(pru))
    if guardar:
        torch.save(red.state_dict(), D.CARPETA / "transformer.pt")
        (D.CARPETA / "transformer_metricas.json").write_text(json.dumps(res, indent=1),
                                                             encoding="utf-8")
    return res


# ══════════════════════════════════════════════════════════════════════
# El juicio: contra las lineas base, con datos que no vio
# ══════════════════════════════════════════════════════════════════════

def juzgar(P, ent, pru, sal) -> dict:
    from materias.red.modelo import auc
    y = P["y"][pru]
    sig = 1 / (1 + np.exp(-sal))
    res = {}
    print("\nRESULTADO EN PRUEBA (datos que la red no vio)")
    for j, nom in enumerate(SALIDAS[:4]):
        m = np.ones(len(pru), bool) if j < 2 else P["valido_toque"][pru]
        a_ = auc(sig[m, j], y[m, j])
        res[nom] = {"auc": a_, "tasa_base": float(y[m, j].mean())}
        print(f"  {nom:16} AUC {a_:.3f}   (0,500 = moneda)  · tasa base {100*y[m, j].mean():.1f} %")
    hh_e = P["hny"][ent].astype(int)
    hh_p = P["hny"][pru].astype(int)
    for j, nom in ((4, "retroceso_largo"), (5, "retroceso_corto"), (6, "rango_1h")):
        real = np.expm1(y[:, j])
        yy = np.expm1(P["y"][ent][:, j])
        med = np.array([np.median(yy[hh_e == h]) if (hh_e == h).any() else np.median(yy)
                        for h in range(24)])
        base = np.abs(med[hh_p] - real).mean()
        red_ = np.abs(np.expm1(sal[:, j]) - real).mean()
        res[nom] = {"error_red": float(red_), "error_tabla_por_hora": float(base)}
        print(f"  {nom:16} error {red_:6.1f} pips · tabla por hora {base:6.1f} "
              f"-> {'GANA la red' if red_ < base else 'gana la tabla'}")
    # El trade de verdad: TODAS las senales, no solo las que el futuro
    # resolvio. La primera version filtraba por `valido_toque` -- quedarse
    # con las operaciones que tocaron objetivo o stop dentro de la hora --
    # y eso es mirar el futuro: en vivo no se sabe de antemano, y el 61 % no
    # toca ninguno y se cierra por tiempo. Con ese filtro la venta salia a
    # +75,7 pips por operacion; simulada como en vivo, a -15,3.
    for j, lado, nom in ((2, 1, "largo"), (3, -1, "corto")):
        for umbral in (0.55, 0.60):
            sel = pru[sig[:, j] > umbral]
            if len(sel) < 30:
                continue
            pips = np.array([_simular(P, i, lado) for i in sel]) / D.PIP - 2.4
            res[f"trade_{nom}_{umbral}"] = {"n": int(len(sel)),
                                            "gana": float((pips > 0).mean()),
                                            "pips_op": float(pips.mean())}
            print(f"  trade {nom} con p>{umbral}: {len(sel):6,} ops · gana "
                  f"{100*(pips > 0).mean():5.1f} % · {pips.mean():+6.1f} pips/op (con spread, sin mirar el futuro)")
    return res


def _simular(P, i: int, lado: int) -> float:
    """Entrada a la apertura de i+1, objetivo y stop a K_ATR; si no toca, cierra por tiempo."""
    v = P["v"]
    ent = v["open"][i + 1]
    d = K_ATR * P["atr"][i]
    for k in range(i + 1, i + 1 + H1):
        sube, baja = v["high"][k] >= ent + d, v["low"][k] <= ent - d
        if lado == 1 and baja or lado == -1 and sube:
            return -d                       # stop primero si tocan los dos: lo prudente
        if lado == 1 and sube or lado == -1 and baja:
            return d
    return lado * (v["close"][i + H1] - ent)


if __name__ == "__main__":
    entrenar()
