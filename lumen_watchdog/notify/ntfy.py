"""ntfy: one JSON publish per run to a private topic, with priority and tags by severity."""

from __future__ import annotations

import os

from ..model import CRITICAL, KIND_ORDER, OK, WARNING, Alert
from .common import HEADINGS, SendError, level, local_time, notice_line, post_json, title

PRIORITY = {CRITICAL: 5, WARNING: 4, OK: 3, "info": 3}
TAGS = {CRITICAL: ["rotating_light"], WARNING: ["warning"], OK: ["white_check_mark"], "info": ["test_tube"]}
MESSAGE_BUDGET = 3800       # ntfy's default message limit is 4096 bytes


def build_payload(alert: Alert, cfg: dict, timezone_label: str = "") -> dict:
    lvl = level(alert)
    if alert.test:
        lines = ["Test message from lumen-watchdog. When this arrives, the ntfy channel is set up."]
    elif alert.failure:
        lines = ["The watchdog run itself failed:", alert.failure[:600],
                 "Look at journalctl -u lumen-watchdog -n 50."]
    else:
        lines = []
        for kind in KIND_ORDER:
            notices = alert.by_kind(kind)
            if not notices:
                continue
            if lines:
                lines.append("")
            lines.append(f"{HEADINGS[kind]}:")
            lines.extend(notice_line(n) for n in notices)
        lines.append("")
        lines.append(f"{alert.open_problems} open · {local_time(alert.created, timezone_label)}")
    message = "\n".join(lines)
    if len(message.encode("utf-8")) > MESSAGE_BUDGET:
        message = message.encode("utf-8")[:MESSAGE_BUDGET].decode("utf-8", "ignore").rsplit("\n", 1)[0] + "\n…"
    payload = {
        "topic": cfg["topic"],
        "title": title(alert),
        "message": message,
        "priority": PRIORITY[lvl],
        "tags": TAGS[lvl],
    }
    if cfg.get("click"):
        payload["click"] = cfg["click"]
    actions = [{"action": "view", "label": a["label"][:40], "url": a["url"], "clear": False}
               for a in cfg.get("actions", [])[:3]]
    if actions:
        payload["actions"] = actions
    return payload


def send(host, alert: Alert, cfg: dict, timezone_label: str = "", environ=None) -> str:
    environ = os.environ if environ is None else environ
    token = environ.get(cfg.get("token_env", "NTFY_TOKEN"), "")
    if not cfg.get("url") or not cfg.get("topic"):
        raise SendError("ntfy: url and topic must be set")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    reply = post_json(host, cfg["url"].rstrip("/"), build_payload(alert, cfg, timezone_label),
                      headers=headers, what="ntfy")
    return str(reply.get("id", ""))
