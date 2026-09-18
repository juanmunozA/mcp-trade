"""
Tests del rescate de DNS. Sin red: se simula el resolvedor.

Lo que se protege: que el parche solo toque los hostnames rescatados y
deje todo lo demás intacto. Un parche de `getaddrinfo` demasiado goloso
rompería la conexión a Postgres, a Gemini y a todo lo demás.
"""

from __future__ import annotations

import socket

import pytest

from core import net


@pytest.fixture(autouse=True)
def _clean():
    net.reset()
    yield
    net.reset()


class FakeSettings:
    clob_host = "https://clob.polymarket.com"
    gamma_host = "https://gamma-api.polymarket.com"
    clob_ws = "wss://ws-subscriptions-clob.polymarket.com/ws"


def test_extrae_hostnames_de_las_urls():
    assert net.hosts_from_settings(FakeSettings()) == [
        "clob.polymarket.com",
        "gamma-api.polymarket.com",
        "ws-subscriptions-clob.polymarket.com",
    ]


async def test_red_sana_no_toca_nada(monkeypatch):
    """En Oracle o en cualquier red normal, esto debe ser un no-op."""
    monkeypatch.setattr(net, "system_resolves", lambda h: True)
    original = socket.getaddrinfo

    rescued = await net.ensure_resolvable("ejemplo.com")

    assert rescued == {}
    assert socket.getaddrinfo is original      # el parche ni se instaló


async def test_rescata_solo_lo_bloqueado(monkeypatch):
    bloqueados = {"bloqueado.com"}
    monkeypatch.setattr(net, "system_resolves", lambda h: h not in bloqueados)

    async def fake_doh(hostname):
        return "1.2.3.4"

    monkeypatch.setattr(net, "resolve_via_doh", fake_doh)

    rescued = await net.ensure_resolvable("ok.com", "bloqueado.com")
    assert rescued == {"bloqueado.com": "1.2.3.4"}


async def test_el_parche_no_afecta_otros_hosts(monkeypatch):
    """El riesgo real: romper Postgres o Gemini con un parche demasiado amplio."""
    monkeypatch.setattr(net, "system_resolves", lambda h: h != "bloqueado.com")

    async def fake_doh(hostname):
        return "1.2.3.4"

    monkeypatch.setattr(net, "resolve_via_doh", fake_doh)
    await net.ensure_resolvable("bloqueado.com")

    llamadas = []
    original = net._original_getaddrinfo

    def espia(host, port, *a, **kw):
        llamadas.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("9.9.9.9", port))]

    net._original_getaddrinfo = espia

    socket.getaddrinfo("bloqueado.com", 443)
    socket.getaddrinfo("neon.tech", 443)

    net._original_getaddrinfo = original

    assert llamadas[0] == "1.2.3.4"        # redirigido
    assert llamadas[1] == "neon.tech"      # intacto


async def test_doh_caido_no_revienta(monkeypatch):
    """Sin conexión no se lanza excepción: se registra y se sigue."""
    monkeypatch.setattr(net, "system_resolves", lambda h: False)

    async def fake_doh(hostname):
        return None

    monkeypatch.setattr(net, "resolve_via_doh", fake_doh)

    assert await net.ensure_resolvable("loquesea.com") == {}


async def test_reset_restaura_getaddrinfo(monkeypatch):
    original = socket.getaddrinfo
    monkeypatch.setattr(net, "system_resolves", lambda h: False)

    async def fake_doh(hostname):
        return "1.2.3.4"

    monkeypatch.setattr(net, "resolve_via_doh", fake_doh)
    await net.ensure_resolvable("x.com")
    assert socket.getaddrinfo is not original

    net.reset()
    assert socket.getaddrinfo is original
