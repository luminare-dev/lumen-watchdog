"""Mail: Postfix queue size and oldest message, IMAP/LMTP/SMTP endpoints, Rspamd ping."""

from __future__ import annotations

import json

from ..model import CRITICAL, Result, grade
from .util import age_text

NAME = "mail"


def parse_queue(stdout: str) -> tuple[int, float | None, dict]:
    """Count the messages in ``postqueue -j`` output; return (count, oldest arrival, per queue)."""
    count = 0
    oldest: float | None = None
    per_queue: dict[str, int] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        count += 1
        queue = str(msg.get("queue_name", "?"))
        per_queue[queue] = per_queue.get(queue, 0) + 1
        arrival = msg.get("arrival_time")
        if isinstance(arrival, (int, float)) and (oldest is None or arrival < oldest):
            oldest = float(arrival)
    return count, oldest, per_queue


def check_queue(cfg: dict, host) -> list[Result]:
    out = host.run([cfg.get("postqueue", "/usr/sbin/postqueue"), "-j"], timeout=60)
    if out.returncode != 0:
        return [Result(NAME, "mail.queue", CRITICAL, "Postfix queue cannot be read",
                       detail=(out.stderr.strip() or f"postqueue exit {out.returncode}")[:300])]
    count, oldest, per_queue = parse_queue(out.stdout)
    results = []
    sev = grade(count, cfg.get("queue_warn"), cfg.get("queue_crit"))
    if sev:
        parts = ", ".join(f"{q} {n}" for q, n in sorted(per_queue.items()))
        results.append(Result(NAME, "mail.queue", sev, f"Postfix queue holds {count} messages ({parts})"))
    if oldest is not None:
        age_min = (host.now() - oldest) / 60
        sev = grade(age_min, cfg.get("age_warn_minutes"), cfg.get("age_crit_minutes"))
        if sev:
            results.append(Result(NAME, "mail.queue_age", sev,
                                  f"oldest queued message is {age_text(age_min * 60)} old"))
    return results


def check_endpoint(ep: dict, host, timeout: float) -> Result | None:
    name = ep["name"]
    key = f"mail.endpoint:{name}"
    expect = ep.get("expect", "")
    quit_line = (ep.get("quit", "") + "\r\n").encode() if ep.get("quit") else b""
    where = ep.get("unix") or f"{ep.get('host')}:{ep.get('port')}"
    try:
        banner, _ms = host.banner(ep.get("host", ""), int(ep.get("port", 0) or 0), use_tls=bool(ep.get("tls")),
                                  timeout=timeout, unix=ep.get("unix", ""), quit_line=quit_line)
    except OSError as exc:
        return Result(NAME, key, CRITICAL, f"{name} is not reachable ({where})", detail=_os_reason(exc))
    if expect and not banner.startswith(expect):
        return Result(NAME, key, CRITICAL, f"{name} answered unexpectedly ({where})",
                      detail=f"expected {expect!r}, got {banner[:80]!r}")
    return None


def check_rspamd(cfg: dict, host, timeout: float) -> Result | None:
    url = cfg.get("rspamd_url")
    if not url:
        return None
    resp = host.http(url, timeout=timeout)
    expect = cfg.get("rspamd_expect", "pong")
    if resp.error:
        return Result(NAME, "mail.rspamd", CRITICAL, "Rspamd is not responding", detail=resp.error[:200])
    body = resp.body.decode("utf-8", "replace")
    if resp.status != 200 or (expect and expect not in body):
        return Result(NAME, "mail.rspamd", CRITICAL, f"Rspamd answered HTTP {resp.status}",
                      detail=body.strip()[:120])
    return None


def run(cfg: dict, host, data: dict) -> list[Result]:
    timeout = float(cfg.get("timeout_seconds", 5))
    results = check_queue(cfg, host) if cfg.get("postqueue") else []
    for ep in cfg.get("endpoints", []):
        r = check_endpoint(ep, host, timeout)
        if r:
            results.append(r)
    r = check_rspamd(cfg, host, timeout)
    if r:
        results.append(r)
    return results


def _os_reason(exc: OSError) -> str:
    return exc.strerror or str(exc) or type(exc).__name__
