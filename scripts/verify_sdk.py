"""
Comprueba que el SDK instalado expone lo que el adapter espera.

    python -m scripts.verify_sdk

Corre esto ANTES de operar, y otra vez cada vez que actualices el SDK.
El adapter (`core/polymarket/client.py`) está escrito contra CLOB **V2**;
si Polymarket renombra algo, prefieres enterarte aquí y no con una posición
abierta.
"""

from __future__ import annotations

import importlib
import inspect
import sys

PKG = "py_clob_client_v2"

# (módulo, símbolo) — lo que LiveClient importa de verdad
REQUIRED_SYMBOLS = [
    ("client", "ClobClient"),
    ("client", "Side"),
    ("clob_types", "OrderArgsV2"),
    ("clob_types", "OrderType"),
    ("clob_types", "OrderPayload"),
    ("clob_types", "TradeParams"),
    ("clob_types", "BalanceAllowanceParams"),
    ("clob_types", "AssetType"),
]

# Métodos que LiveClient llama
REQUIRED_METHODS = [
    "set_api_creds",
    "create_or_derive_api_key",
    "create_and_post_order",
    "cancel_order",
    "cancel_all",
    "get_trades",
    "get_address",
    "get_tick_size",
    "get_closed_only_mode",
    "get_balance_allowance",
    "update_balance_allowance",
]


def main() -> int:
    # El error más caro: tener instalado el paquete V1 por descuido.
    if importlib.util.find_spec("py_clob_client") is not None:
        print(
            "  AVISO  `py-clob-client` (V1) está instalado.\n"
            "         V1 dejó de funcionar contra producción el 2026-04-28.\n"
            "         Desinstálalo para no importarlo por accidente:\n"
            "           pip uninstall py-clob-client\n"
        )

    if importlib.util.find_spec(PKG) is None:
        print(f"{PKG} no está instalado:  pip install py-clob-client-v2")
        return 1

    pkg = importlib.import_module(PKG)
    print(f"{PKG} versión {getattr(pkg, '__version__', 'desconocida')}\n")

    ok = True

    for module_suffix, symbol in REQUIRED_SYMBOLS:
        module_name = f"{PKG}.{module_suffix}"
        try:
            module = importlib.import_module(module_name)
            getattr(module, symbol)
            print(f"  OK    {module_name}.{symbol}")
        except (ImportError, AttributeError) as exc:
            print(f"  FALTA {module_name}.{symbol}  ({exc})")
            ok = False

    print()
    from py_clob_client_v2.client import ClobClient

    # El cambio silencioso más peligroso: chain_id pasó a ser posicional.
    init_params = list(inspect.signature(ClobClient.__init__).parameters)
    if init_params[1:3] != ["host", "chain_id"]:
        print(f"  FALTA firma del constructor cambió: {init_params[:4]}")
        ok = False
    else:
        print("  OK    ClobClient(host, chain_id, ...)  ← chain_id posicional")

    for name in REQUIRED_METHODS:
        method = getattr(ClobClient, name, None)
        if method is None:
            print(f"  FALTA ClobClient.{name}()")
            ok = False
            continue
        try:
            sig = inspect.signature(method)
            params = ", ".join(list(sig.parameters)[1:]) or "—"
        except (TypeError, ValueError):
            params = "?"
        print(f"  OK    ClobClient.{name}({params[:70]})")

    print()
    if ok:
        print("Todo lo que el adapter usa existe en el SDK instalado.")
        return 0

    print(
        "Hay diferencias. Todo lo que toca el SDK vive en\n"
        "  core/polymarket/client.py → clase LiveClient\n"
        "Arréglalo ahí y vuelve a correr esto. No operes hasta que pase."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
