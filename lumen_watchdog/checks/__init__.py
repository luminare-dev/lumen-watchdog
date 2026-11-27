"""Check registry: name -> function(cfg, host, data) -> list[Result]."""

from __future__ import annotations

from . import mail, oom, resources, system, systemd, web

REGISTRY = {
    "systemd": systemd.run,
    "oom": oom.run,
    "mail": mail.run,
    "disk": resources.run_disk,
    "memory": resources.run_memory,
    "load": resources.run_load,
    "tls": web.run_tls,
    "http": web.run_http,
    "backups": system.run_backups,
    "apt": system.run_apt,
    "quota": system.run_quota,
}

DESCRIPTIONS = {
    "systemd": "failed units, watched units that must be active, restart counters",
    "oom": "OOM kills in the kernel log since the last run",
    "mail": "Postfix queue size and age, IMAP/LMTP/SMTP endpoints, Rspamd ping",
    "disk": "space and inodes per mount",
    "memory": "available memory, swap use, memory pressure (PSI)",
    "load": "5-minute load per CPU",
    "tls": "certificate expiry and trust on live endpoints",
    "http": "status, expected text and latency of URLs",
    "backups": "age of the newest file in backup folders",
    "apt": "pending security updates and reboot-required",
    "quota": "user and group quotas switched on",
}
