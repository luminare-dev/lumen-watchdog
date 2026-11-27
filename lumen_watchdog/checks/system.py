"""Backups (newest file age), apt (security updates, reboot required) and disk quotas."""

from __future__ import annotations

import re

from ..model import CRITICAL, Result, WARNING, grade, parse_severity
from .util import age_text

APT_GET = "/usr/bin/apt-get"
QUOTAON = "/usr/sbin/quotaon"


def run_backups(cfg: dict, host, data: dict) -> list[Result]:
    results = []
    for d in cfg.get("dirs", []):
        path = d["path"]
        name = d.get("name") or path
        key = f"backups:{name}"
        max_age_h = float(d.get("max_age_hours", 26))
        try:
            newest, _seen = host.newest_mtime(path, d.get("pattern", "*"), bool(d.get("recursive", True)),
                                              int(d.get("max_entries", cfg.get("max_entries", 20000))))
        except OSError as exc:
            results.append(Result("backups", key, CRITICAL, f"backup folder {name} cannot be read",
                                  detail=exc.strerror or str(exc)))
            continue
        if newest is None:
            results.append(Result("backups", key, CRITICAL, f"backup folder {name} holds no backup files"))
            continue
        age_h = (host.now() - newest) / 3600
        crit_h = float(d.get("crit_age_hours", max_age_h * 2))
        sev = grade(age_h, max_age_h, crit_h)
        if sev:
            results.append(Result("backups", key, sev, f"newest backup in {name} is {age_text(age_h * 3600)} old",
                                  detail=f"limit {max_age_h:.0f} h"))
    return results


SECURITY_INST = re.compile(r"^Inst (\S+) .*\(([^)]*)\)")


def parse_apt_simulation(stdout: str) -> tuple[list[str], int]:
    """Packages from ``apt-get -s dist-upgrade``: (security upgrades, all upgrades)."""
    security: list[str] = []
    total = 0
    for line in stdout.splitlines():
        if not line.startswith("Inst "):
            continue
        total += 1
        m = SECURITY_INST.match(line)
        if m and "-security" in m.group(2):
            security.append(m.group(1))
    return security, total


def run_apt(cfg: dict, host, data: dict) -> list[Result]:
    results = []
    out = host.run([APT_GET, "-s", "-q", "-o", "Debug::NoLocking=true", "dist-upgrade"], timeout=180)
    if out.returncode != 0:
        results.append(Result("apt", "apt.updates", WARNING, "pending updates cannot be read",
                              detail=out.stderr.strip()[:200]))
    else:
        security, total = parse_apt_simulation(out.stdout)
        crit = cfg.get("security_crit") or None
        sev = grade(len(security), cfg.get("security_warn"), crit)
        if security and sev:
            shown = ", ".join(security[:8]) + (" …" if len(security) > 8 else "")
            results.append(Result("apt", "apt.security", sev,
                                  f"{len(security)} security update{'s' if len(security) != 1 else ''} pending"
                                  f" ({total} in all)",
                                  detail=shown))
    mode = cfg.get("reboot_required", "warning")
    if mode != "off" and host.exists("/var/run/reboot-required"):
        pkgs = (host.read_text("/var/run/reboot-required.pkgs") or "").split()
        detail = ", ".join(sorted(set(pkgs))[:8])
        results.append(Result("apt", "apt.reboot", parse_severity(mode), "a reboot is required", detail=detail))
    return results


QUOTA_LINE = re.compile(r"^(user|group|project) quota on (\S+) \((\S+)\) is (on|off)")


def parse_quotaon(stdout: str) -> dict[tuple[str, str], bool]:
    states = {}
    for line in stdout.splitlines():
        m = QUOTA_LINE.match(line.strip())
        if m:
            states[(m.group(2), m.group(1))] = m.group(4) == "on"
    return states


def run_quota(cfg: dict, host, data: dict) -> list[Result]:
    out = host.run([QUOTAON, "-pa"], timeout=30)
    # quotaon -p exits non-zero when a quota is off; the text is what counts.
    states = parse_quotaon(out.stdout + "\n" + out.stderr)
    sev = parse_severity(cfg.get("severity", "warning"))
    results = []
    for mount in cfg.get("mounts", ["/"]):
        for qtype in cfg.get("types", ["user", "group"]):
            state = states.get((mount, qtype))
            if state is None:
                results.append(Result("quota", f"quota:{mount}:{qtype}", sev,
                                      f"{qtype} quota on {mount}: no quota information"))
            elif not state:
                results.append(Result("quota", f"quota:{mount}:{qtype}", sev, f"{qtype} quota on {mount} is off"))
    return results
