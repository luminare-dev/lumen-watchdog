"""Channels: Discord (Components V2 webhook) and ntfy."""

from __future__ import annotations

import json

from ..model import Alert
from . import discord, ntfy
from .common import SendError, for_channel

CHANNELS = ("discord", "ntfy")


def enabled_channels(cfg: dict) -> list[str]:
    return [c for c in CHANNELS if cfg[c].get("enabled")]


def render(channel: str, alert: Alert, cfg: dict, environ=None) -> dict:
    """The payload a channel would send, for --dry-run and the snapshot tests."""
    part = for_channel(alert, cfg[channel].get("min_severity", "warning"))
    if channel == "discord":
        _webhook, ping = discord.credentials(cfg["discord"], environ)
        return discord.build_payload(part, cfg["discord"], ping, cfg["watchdog"]["interval_minutes"])
    return ntfy.build_payload(part, cfg["ntfy"], cfg["watchdog"].get("timezone", ""))


def deliver(host, alert: Alert, cfg: dict, channels: list[str], environ=None) -> dict[str, str]:
    """Send to each channel that has something to say. Returns {channel: "sent ..." or error text}."""
    status: dict[str, str] = {}
    for channel in channels:
        part = for_channel(alert, cfg[channel].get("min_severity", "warning"))
        if not part.notices and not part.test and not part.failure:
            status[channel] = "nothing to send"
            continue
        try:
            if channel == "discord":
                msg_id = discord.send(host, part, cfg["discord"], cfg["watchdog"]["interval_minutes"], environ)
            else:
                msg_id = ntfy.send(host, part, cfg["ntfy"], cfg["watchdog"].get("timezone", ""), environ)
            status[channel] = f"sent{f' (message {msg_id})' if msg_id else ''}"
        except SendError as exc:
            status[channel] = f"error: {exc}"
    return status


def dumps(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
