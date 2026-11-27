"""Shared pieces of the channels: filtering by severity, wording, colours and HTTP with retries."""

from __future__ import annotations

import json
import time

from ..model import (CRITICAL, ESCALATED, EVENT, NEW, OK, RECOVERED, REMINDER, WARNING, Alert, Notice,
                     parse_severity)

COLORS = {  # Luminare palette: Ember bright, Amber bright, Verdant bright, Lumen
    CRITICAL: 0xFF5147,
    WARNING: 0xFFB334,
    OK: 0x58DD70,
    "info": 0x5BDFFF,
}
DOTS = {CRITICAL: "🔴", WARNING: "🟠", OK: "🟢"}
HEADINGS = {
    NEW: "New problems",
    ESCALATED: "Worse than before",
    EVENT: "Events",
    REMINDER: "Still open",
    RECOVERED: "Recovered",
}
LEVEL_WORDS = {CRITICAL: "Critical", WARNING: "Warning", OK: "Recovered"}


class SendError(RuntimeError):
    """A channel could not deliver. The message never contains the webhook URL or a token."""


def for_channel(alert: Alert, min_severity: str) -> Alert:
    """The part of ``alert`` this channel wants (recoveries follow the severity they had)."""
    if alert.test or alert.failure:
        return alert
    floor = parse_severity(min_severity)
    notices = [n for n in alert.notices if n.result.severity >= floor]
    return Alert(alert.host, notices, alert.open_problems, alert.test, alert.failure, alert.created)


def level(alert: Alert):
    if alert.test:
        return "info"
    if alert.failure:
        return CRITICAL
    return OK if alert.only_recoveries else alert.severity


def title(alert: Alert) -> str:
    """'Critical: 2 new problems on host', 'Recovered: 1 problem on host', 'Test message from host'."""
    if alert.test:
        return f"Test message from {alert.host}"
    if alert.failure:
        return f"Watchdog run failed on {alert.host}"
    counts = []
    for kind, one, many in ((NEW, "new problem", "new problems"), (ESCALATED, "got worse", "got worse"),
                            (EVENT, "event", "events"), (REMINDER, "still open", "still open"),
                            (RECOVERED, "recovered", "recovered")):
        n = len(alert.by_kind(kind))
        if n:
            counts.append(f"{n} {one if n == 1 else many}")
    return f"{LEVEL_WORDS[level(alert)]} on {alert.host}: {', '.join(counts)}"


def notice_line(n: Notice) -> str:
    dot = DOTS[OK] if n.kind == RECOVERED else DOTS.get(n.result.severity, "⚪")
    return f"{dot} {n.result.summary}"


def post_json(host, url: str, payload: dict, *, headers: dict | None = None, timeout: float = 15.0,
              attempts: int = 4, max_wait: float = 60.0, what: str = "channel") -> dict:
    """POST JSON with retries: 429 waits ``retry_after`` (body or header), 5xx and network errors back off."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    last = ""
    for attempt in range(1, attempts + 1):
        resp = host.http(url, method="POST", data=data, headers=hdrs, timeout=timeout, follow_redirects=False)
        if resp.error:
            last = f"network error: {resp.error}"
            wait = min(max_wait, 2 ** attempt)
        elif 200 <= resp.status < 300:
            try:
                return json.loads(resp.body or b"{}")
            except json.JSONDecodeError:
                return {}
        elif resp.status == 429:
            wait = _retry_after(resp)
            last = f"rate limited (429), retry after {wait:.1f} s"
            if wait > max_wait:
                raise SendError(f"{what}: {last}, longer than {max_wait:.0f} s")
        elif resp.status >= 500:
            last = f"HTTP {resp.status}"
            wait = min(max_wait, 2 ** attempt)
        else:
            body = resp.body.decode("utf-8", "replace")[:500]
            raise SendError(f"{what}: HTTP {resp.status}: {body}")
        if attempt < attempts:
            host.sleep(wait)
    raise SendError(f"{what}: gave up after {attempts} attempts ({last})")


def _retry_after(resp) -> float:
    try:
        body = json.loads(resp.body or b"{}")
        if isinstance(body, dict) and "retry_after" in body:
            return float(body["retry_after"]) + 0.25
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    for header in ("retry-after", "x-ratelimit-reset-after"):
        if header in resp.headers:
            try:
                return float(resp.headers[header]) + 0.25
            except ValueError:
                pass
    return 5.0


def local_time(ts: float, label: str = "") -> str:
    text = time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))
    return f"{text} {label}".strip()
