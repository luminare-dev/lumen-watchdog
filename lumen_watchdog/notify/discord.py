"""Discord webhook with a Components V2 message.

Components V2 (message flag 1 << 15) replaces ``content`` and ``embeds`` with layout components:
a Container (type 17) with an accent colour, Sections (9) with a Thumbnail (11), Text Displays (10),
Separators (14) and an Action Row (1) of link buttons (2, style 5). A webhook that no application
owns may send these non-interactive components only when the execute URL carries
``?with_components=true``. Limits: 40 components and 4000 characters of text per message.
"""

from __future__ import annotations

import os
import re
import urllib.parse

from .. import __version__
from ..model import RECOVERED, Alert, KIND_ORDER
from .common import COLORS, HEADINGS, SendError, level, notice_line, post_json, title

IS_COMPONENTS_V2 = 1 << 15
TEXT_BUDGET = 3800          # of Discord's 4000 characters, leaving room for the header and footer
MAX_COMPONENTS = 40
SNOWFLAKE = re.compile(r"^[0-9]{17,20}$")


def _text(content: str) -> dict:
    return {"type": 10, "content": content}


def _sep(spacing: int = 1, divider: bool = True) -> dict:
    return {"type": 14, "divider": divider, "spacing": spacing}


def build_payload(alert: Alert, cfg: dict, ping_user: str = "", interval_minutes: int = 5) -> dict:
    """The JSON body for the execute-webhook call."""
    color = COLORS[level(alert)]
    avatar = cfg.get("avatar_url", "")
    thumb = cfg.get("thumbnail_url") or avatar
    used = 0

    if alert.test:
        lines = ["This is a test message from lumen-watchdog. When it shows the coloured box, "
                 "the buttons and the mention, the Discord channel is set up."]
    elif alert.failure:
        lines = ["The watchdog run itself failed, so the checks did not finish.",
                 f"```\n{alert.failure[:600]}\n```",
                 "Look at `journalctl -u lumen-watchdog -n 50` on the server."]
    else:
        lines = []

    head_text = f"## {title(alert)}"
    if not alert.test and not alert.failure:
        head_text += f"\n{alert.open_problems} open problem{'s' if alert.open_problems != 1 else ''} after this check"
    used += len(head_text)

    head: dict = {"type": 9, "components": [_text(head_text)]}
    if thumb:
        head["accessory"] = {"type": 11, "media": {"url": thumb}, "description": "Lumen Watchdog"}
    else:
        head = _text(head_text)
    inner: list[dict] = [head, _sep()]

    if lines:
        body = "\n".join(lines)
        inner.append(_text(body))
        used += len(body)

    footer = (f"-# lumen-watchdog {__version__} · {alert.host} · <t:{int(alert.created)}:f>"
              f" · checks every {interval_minutes} min")
    used += len(footer)

    # Grouped notices, cut to the text budget and the component limit.
    groups = [(kind, alert.by_kind(kind)) for kind in KIND_ORDER if alert.by_kind(kind)]
    remaining = sum(len(g) for _, g in groups)
    for kind, notices in groups:
        if len(inner) + 4 > MAX_COMPONENTS - 2:
            break
        out = [f"**{HEADINGS[kind]}**"]
        for n in notices:
            line = notice_line(n)
            if n.kind != RECOVERED and n.result.detail:
                line += f"\n-# {n.result.detail[:200]}"
            if n.since and n.kind in ("reminder", "escalated", "recovered"):
                line += f" · since <t:{int(n.since)}:R>"
            if used + len(line) + 40 > TEXT_BUDGET:
                break
            out.append(line)
            used += len(line) + 1
            remaining -= 1
        inner.append(_text("\n".join(out)))
        used += len(out[0])
    if remaining > 0:
        more = f"-# … and {remaining} more"
        inner.append(_text(more))
        used += len(more)

    inner.append(_sep(spacing=1))
    inner.append(_text(footer))
    buttons = [{"type": 2, "style": 5, "label": b["label"][:80], "url": b["url"][:512]}
               for b in cfg.get("buttons", [])[:5]]
    if buttons:
        inner.append({"type": 1, "components": buttons})

    components: list[dict] = []
    allowed: dict = {"parse": []}
    if ping_user and SNOWFLAKE.match(ping_user):
        components.append(_text(f"<@{ping_user}> {title(alert)}"))
        allowed["users"] = [ping_user]
    components.append({"type": 17, "accent_color": color, "components": inner})

    payload = {
        "username": cfg.get("username", "Lumen Watchdog")[:80],
        "flags": IS_COMPONENTS_V2,
        "allowed_mentions": allowed,
        "components": components,
    }
    if avatar:
        payload["avatar_url"] = avatar
    return payload


def execute_url(webhook: str) -> str:
    """Add ``wait=true`` and ``with_components=true`` to the webhook's execute URL."""
    parts = urllib.parse.urlsplit(webhook)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update({"wait": "true", "with_components": "true"})
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def credentials(cfg: dict, environ=None) -> tuple[str, str]:
    environ = os.environ if environ is None else environ
    return environ.get(cfg.get("webhook_env", "DISCORD_WEBHOOK_URL"), ""), \
        environ.get(cfg.get("ping_env", "DISCORD_PING_USER_ID"), "").strip()


def send(host, alert: Alert, cfg: dict, interval_minutes: int = 5, environ=None) -> str:
    webhook, ping = credentials(cfg, environ)
    if not webhook:
        raise SendError(f"discord: {cfg.get('webhook_env', 'DISCORD_WEBHOOK_URL')} is not set")
    if not webhook.startswith("https://"):
        raise SendError("discord: the webhook URL must start with https://")
    payload = build_payload(alert, cfg, ping, interval_minutes)
    reply = post_json(host, execute_url(webhook), payload, what="discord")
    return str(reply.get("id", ""))
