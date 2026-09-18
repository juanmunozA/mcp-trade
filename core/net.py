"""
Resolución DNS independiente de la red en la que estés.

EL PROBLEMA
───────────
Algunos ISP bloquean dominios envenenando su propio DNS. Medido en esta
máquina: el resolvedor del router devuelve «Error de servidor DNS» para
`gamma-api.polymarket.com`, mientras que `1.1.1.1` devuelve la IP y la
conexión funciona perfecto (HTTP 200 en 0.16 s). El bloqueo es solo de
nombres; la ruta está abierta.

POR QUÉ NO SE ARREGLA CAMBIANDO EL DNS DEL SISTEMA
──────────────────────────────────────────────────
Porque el bot no vive en una sola red. Se desarrolla en un portátil que
salta entre WiFis, y se despliega en un servidor cuyo DNS sí funciona.
Una configuración manual por adaptador es frágil, no viaja con el código,
y se rompe con portales cautivos.

LA SOLUCIÓN
───────────
Resolver por DNS-over-HTTPS solo cuando el DNS del sistema falla, y meter
el resultado en `socket.getaddrinfo` para el resto del proceso. La URL
sigue diciendo el hostname, así que SNI y cabecera Host salen correctos
y TLS valida normal — es lo mismo que hace `curl --resolve`.

Donde el DNS del sistema funciona (Oracle, cualquier red sana) esto no
hace absolutamente nada.
"""

from __future__ import annotations

import logging
import socket
import time

import httpx

log = logging.getLogger("net")

# Resolvedores DoH, en orden. El primero que responda gana.
DOH_ENDPOINTS = [
    ("https://cloudflare-dns.com/dns-query", "application/dns-json"),
    ("https://dns.google/resolve", "application/json"),
]

TTL_SECONDS = 3600.0

_overrides: dict[str, str] = {}
_resolved_at: dict[str, float] = {}
_original_getaddrinfo = None


# ── DNS del sistema ──────────────────────────────────────────────────────


def system_resolves(hostname: str) -> bool:
    """¿El resolvedor del sistema conoce este nombre?"""
    getaddrinfo = _original_getaddrinfo or socket.getaddrinfo
    try:
        getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
        return True
    except socket.gaierror:
        return False


# ── DNS over HTTPS ───────────────────────────────────────────────────────


async def resolve_via_doh(hostname: str) -> str | None:
    """Primera dirección A (IPv4) que devuelva cualquier resolvedor DoH."""
    async with httpx.AsyncClient(timeout=15) as http:
        for url, accept in DOH_ENDPOINTS:
            try:
                r = await http.get(
                    url,
                    params={"name": hostname, "type": "A"},
                    headers={"Accept": accept},
                )
                if r.status_code != 200:
                    continue
                for answer in r.json().get("Answer", []):
                    if answer.get("type") == 1:      # 1 = registro A
                        return answer["data"]
            except Exception as exc:  # noqa: BLE001 — probamos el siguiente
                log.debug("DoH %s falló para %s: %s", url, hostname, exc)
    return None


# ── el parche ────────────────────────────────────────────────────────────


def _install_patch() -> None:
    """
    Redirige getaddrinfo solo para los hostnames que tenemos cacheados.
    Todo lo demás pasa intacto al resolvedor original.
    """
    global _original_getaddrinfo
    if _original_getaddrinfo is not None:
        return
    _original_getaddrinfo = socket.getaddrinfo

    def patched(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name in _overrides and time.monotonic() - _resolved_at[name] < TTL_SECONDS:
            return _original_getaddrinfo(_overrides[name], port, *args, **kwargs)
        return _original_getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = patched


def reset() -> None:
    """Deshace el parche. Para tests."""
    global _original_getaddrinfo
    if _original_getaddrinfo is not None:
        socket.getaddrinfo = _original_getaddrinfo
        _original_getaddrinfo = None
    _overrides.clear()
    _resolved_at.clear()


# ── API pública ──────────────────────────────────────────────────────────


async def ensure_resolvable(*hostnames: str) -> dict[str, str]:
    """
    Llamar una vez al arrancar, con los dominios que el bot necesita.

    Devuelve solo los que hubo que rescatar por DoH. Diccionario vacío =
    la red está sana y no se tocó nada.
    """
    rescued: dict[str, str] = {}

    for hostname in hostnames:
        if system_resolves(hostname):
            continue

        ip = await resolve_via_doh(hostname)
        if ip is None:
            log.error(
                "%s no resuelve ni por DNS del sistema ni por DoH. "
                "¿Bloqueo a nivel de IP, o sin conexión?",
                hostname,
            )
            continue

        _install_patch()
        _overrides[hostname] = ip
        _resolved_at[hostname] = time.monotonic()
        rescued[hostname] = ip
        log.warning("DNS del sistema no resuelve %s — usando DoH → %s", hostname, ip)

    return rescued


def hosts_from_settings(settings) -> list[str]:
    """Extrae los hostnames de las URLs configuradas."""
    from urllib.parse import urlsplit

    urls = [settings.clob_host, settings.gamma_host, settings.clob_ws]
    return sorted({h for u in urls if (h := urlsplit(u).hostname)})
