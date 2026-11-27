"""Configuration: one TOML file, merged over the defaults below, plus secrets from the environment."""

from __future__ import annotations

import copy
import os
import stat
import tomllib

from .model import parse_severity

CHECK_NAMES = ("systemd", "oom", "mail", "disk", "memory", "load", "tls", "http", "backups", "apt", "quota")

DEFAULTS: dict = {
    "watchdog": {
        "host_name": "",                       # empty: the host's FQDN
        "state_file": "/var/lib/lumen-watchdog/state.json",
        "env_file": "/etc/lumen-watchdog/secrets.env",
        "interval_minutes": 5,                 # how often the timer runs (shown in the footer)
        "timezone_label": "",                  # optional label after the time, e.g. "CET"
    },
    "defaults": {
        "every_minutes": 0,                    # run the check on every run
        "repeat_minutes": 360,                 # remind about an open problem every 6 h (0: never)
        "cooldown_minutes": 15,                # at most one alert per problem in this time
        "confirm_runs": 1,                     # failed runs in a row before the first alert
        "recoveries": True,                    # send a message when a problem is gone
    },
    "checks": {
        "systemd": {
            "enabled": True,
            "failed_units": True,
            "ignore_failed": [],
            "watch": [],
            "optional": [],                    # watched units that may be missing
            "inactive_severity": "critical",
            "restart_warn": 1,                 # restarts since the last run that raise a warning
            "restart_crit": 5,
        },
        "oom": {"enabled": True, "lookback_minutes": 60, "severity": "critical"},
        "mail": {
            "enabled": True,
            "postqueue": "/usr/sbin/postqueue",
            "queue_warn": 50,
            "queue_crit": 200,
            "age_warn_minutes": 60,
            "age_crit_minutes": 360,
            "rspamd_url": "http://127.0.0.1:11334/ping",
            "rspamd_expect": "pong",
            "timeout_seconds": 5,
            "endpoints": [],
        },
        "disk": {
            "enabled": True,
            "mounts": [],                      # empty: every mounted local file system
            "fstypes": ["ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "vfat"],
            "ignore": [],
            "used_warn_percent": 85,
            "used_crit_percent": 95,
            "inodes_warn_percent": 85,
            "inodes_crit_percent": 95,
        },
        "memory": {
            "enabled": True,
            "available_warn_percent": 10,
            "available_crit_percent": 5,
            "swap_warn_percent": 80,
            "swap_crit_percent": 95,
            "pressure_warn": 10.0,             # /proc/pressure/memory "full" avg60, percent
            "pressure_crit": 30.0,
        },
        "load": {"enabled": True, "per_cpu_warn": 2.0, "per_cpu_crit": 4.0},
        "tls": {"enabled": True, "warn_days": 21, "crit_days": 7, "timeout_seconds": 8,
                "every_minutes": 60, "hosts": [], "targets": []},
        "http": {"enabled": True, "latency_warn_ms": 2500, "latency_crit_ms": 10000,
                 "timeout_seconds": 15, "urls": []},
        "backups": {"enabled": True, "every_minutes": 30, "max_entries": 20000, "dirs": []},
        "apt": {"enabled": True, "every_minutes": 360, "security_warn": 1, "security_crit": 0,
                "reboot_required": "warning", "repeat_minutes": 1440},
        "quota": {"enabled": True, "every_minutes": 30, "mounts": ["/"], "types": ["user", "group"],
                  "severity": "warning"},
    },
    "discord": {
        "enabled": True,
        "webhook_env": "DISCORD_WEBHOOK_URL",
        "ping_env": "DISCORD_PING_USER_ID",
        "username": "Lumen Watchdog",
        "avatar_url": "https://raw.githubusercontent.com/luminare-dev/lumen-watchdog/main/docs/brand/avatar-256.png",
        "thumbnail_url": "",                   # empty: the avatar
        "min_severity": "warning",
        "buttons": [],
    },
    "ntfy": {
        "enabled": False,
        "url": "",
        "topic": "",
        "token_env": "NTFY_TOKEN",
        "click": "",
        "min_severity": "warning",
        "actions": [],
    },
}

PER_CHECK_KEYS = ("every_minutes", "repeat_minutes", "cooldown_minutes", "confirm_runs", "recoveries")


class ConfigError(ValueError):
    pass


def _merge(base: dict, over: dict, path: str = "") -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        here = f"{path}.{key}" if path else key
        if key not in base:
            # Per-check overrides of the [defaults] keys are allowed in every check table.
            if path.startswith("checks.") and key in PER_CHECK_KEYS:
                out[key] = value
                continue
            raise ConfigError(f"unknown setting {here}")
        if isinstance(base[key], dict) and isinstance(value, dict):
            out[key] = _merge(base[key], value, here)
        else:
            out[key] = value
    return out


def load(path: str | None, text: str | None = None) -> dict:
    """Load and validate a configuration file (or ``text``); missing settings take the defaults."""
    if text is None:
        if path is None:
            data = {}
        else:
            try:
                with open(path, "rb") as fh:
                    data = tomllib.load(fh)
            except FileNotFoundError:
                raise ConfigError(f"{path}: no such file") from None
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{path}: {exc}") from None
    else:
        data = tomllib.loads(text)
    cfg = _merge(DEFAULTS, data)
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    for name in ("discord", "ntfy"):
        parse_severity(cfg[name]["min_severity"])
    parse_severity(cfg["checks"]["systemd"]["inactive_severity"])
    parse_severity(cfg["checks"]["oom"]["severity"])
    parse_severity(cfg["checks"]["quota"]["severity"])
    if cfg["checks"]["apt"]["reboot_required"] not in ("off", "warning", "critical"):
        raise ConfigError("checks.apt.reboot_required must be off, warning or critical")
    buttons = cfg["discord"]["buttons"]
    if len(buttons) > 5:
        raise ConfigError("discord.buttons: at most 5 link buttons")
    for b in buttons:
        if not str(b.get("url", "")).startswith(("https://", "http://")) or not b.get("label"):
            raise ConfigError("discord.buttons: every button needs a label and an http(s) url")
    if len(cfg["ntfy"]["actions"]) > 3:
        raise ConfigError("ntfy.actions: ntfy shows at most 3 actions")
    if cfg["ntfy"]["enabled"] and (not cfg["ntfy"]["url"] or not cfg["ntfy"]["topic"]):
        raise ConfigError("ntfy: url and topic are required when ntfy is enabled")
    for i, ep in enumerate(cfg["checks"]["mail"]["endpoints"]):
        if not ep.get("name") or not (ep.get("unix") or (ep.get("host") and ep.get("port"))):
            raise ConfigError(f"checks.mail.endpoints[{i}]: needs a name and host+port or unix")
    for i, u in enumerate(cfg["checks"]["http"]["urls"]):
        if not u.get("url"):
            raise ConfigError(f"checks.http.urls[{i}]: url is required")
    for i, d in enumerate(cfg["checks"]["backups"]["dirs"]):
        if not d.get("path"):
            raise ConfigError(f"checks.backups.dirs[{i}]: path is required")


def check_settings(cfg: dict, name: str) -> dict:
    """The [defaults] values with the check's own overrides applied."""
    own = cfg["checks"][name]
    merged = dict(cfg["defaults"])
    for key in PER_CHECK_KEYS:
        if key in own:
            merged[key] = own[key]
    return merged


def load_env_file(path: str, environ: dict | None = None) -> list[str]:
    """Read KEY=value lines into ``environ`` (keys already set win). Returns warnings.

    The file holds the webhook URL and tokens, so it must belong to root and not be readable by
    others; otherwise it is skipped with a warning.
    """
    environ = os.environ if environ is None else environ
    warnings: list[str] = []
    if not path or not os.path.exists(path):
        return warnings
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO) or st.st_uid not in (0, os.getuid()):
        warnings.append(f"{path}: skipped, must be owned by root with mode 600")
        return warnings
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            environ.setdefault(key, value)
    return warnings
