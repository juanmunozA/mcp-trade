"""
Alertas a Telegram.

Regla: una alerta que se ignora es peor que ninguna, porque te entrena a
ignorar las siguientes. Solo tres niveles, y `CRITICAL` se reserva para
cosas que exigen que dejes lo que estés haciendo:
kill switch, desincronización de posición, datos congelados.
"""

from __future__ import annotations

import logging
from enum import StrEnum

import httpx

log = logging.getLogger("alerts")


class Level(StrEnum):
    INFO = "ℹ️"
    WARN = "⚠️"
    CRITICAL = "🚨"


class Telegram:
    def __init__(self, token: str | None, chat_id: str | None) -> None:
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        if not self.enabled:
            log.warning("Telegram sin configurar: las alertas solo van al log")

    async def send(self, level: Level, title: str, body: str = "") -> None:
        text = f"{level} *{_esc(title)}*"
        if body:
            text += f"\n```\n{body}\n```"

        log_at = logging.CRITICAL if level is Level.CRITICAL else logging.INFO
        log.log(log_at, "%s %s | %s", level, title, body)

        if not self.enabled:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                await http.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "MarkdownV2",
                    },
                )
        except Exception as exc:  # noqa: BLE001
            # Una alerta que falla nunca puede tumbar el bot.
            log.error("no se pudo enviar la alerta a Telegram: %s", exc)

    async def killed(self, pilot: str, reason: str, snapshot: dict) -> None:
        await self.send(
            Level.CRITICAL,
            f"PILOTO {pilot.upper()} DETENIDO",
            f"{reason}\n\n" + "\n".join(f"{k}: {v}" for k, v in snapshot.items()),
        )

    async def daily_report(self, pilot: str, stats: dict, rejections: list[dict]) -> None:
        lines = [f"{k}: {v}" for k, v in stats.items() if v is not None]
        if rejections:
            lines.append("")
            lines.append("rechazos:")
            lines += [f"  {r['reject_code']}: {r['n']}" for r in rejections]
        await self.send(Level.INFO, f"Reporte diario · piloto {pilot.upper()}", "\n".join(lines))


def _esc(s: str) -> str:
    """MarkdownV2 de Telegram exige escapar casi todo."""
    for ch in r"_*[]()~`>#+-=|{}.!":
        s = s.replace(ch, "\\" + ch)
    return s
