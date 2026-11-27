"""systemd: failed units, a watch list of units that must be active, and restart counters."""

from __future__ import annotations

import fnmatch

from ..model import CRITICAL, OK, WARNING, Result, grade, parse_severity

NAME = "systemd"
GLOB_CHARS = "*?["
SYSTEMCTL = "/usr/bin/systemctl"


def _units_from_list(stdout: str) -> list[str]:
    units = []
    for line in stdout.splitlines():
        parts = line.split()
        if parts and parts[0] in ("●", "*"):
            parts = parts[1:]
        if parts:
            units.append(parts[0])
    return units


def parse_show(stdout: str) -> dict[str, dict[str, str]]:
    """Parse ``systemctl show -p ...`` output for several units (blocks split by blank lines)."""
    units: dict[str, dict[str, str]] = {}
    block: dict[str, str] = {}
    for line in stdout.splitlines() + [""]:
        if not line.strip():
            if block.get("Id"):
                units[block["Id"]] = block
            block = {}
            continue
        key, _, value = line.partition("=")
        block[key] = value
    return units


def expand(host, patterns: list[str]) -> tuple[list[str], list[str]]:
    """Expand glob patterns to unit names. Returns (units, patterns without a match)."""
    units: list[str] = []
    unmatched: list[str] = []
    for pattern in patterns:
        if not any(c in pattern for c in GLOB_CHARS):
            units.append(pattern)
            continue
        # A pattern matches the units systemd has loaded (running, failed or recently stopped),
        # not every installed unit file: an installed but unused PHP version is not a problem.
        out = host.run([SYSTEMCTL, "list-units", "--all", "--plain", "--no-legend", "--no-pager", pattern])
        found = {u for u in _units_from_list(out.stdout) if fnmatch.fnmatch(u, pattern)}
        if found:
            units.extend(sorted(found))
        else:
            unmatched.append(pattern)
    seen: set[str] = set()
    return [u for u in units if not (u in seen or seen.add(u))], unmatched


def run(cfg: dict, host, data: dict) -> list[Result]:
    results: list[Result] = []
    ignore = cfg.get("ignore_failed", [])

    failed: set[str] = set()
    if cfg.get("failed_units", True):
        out = host.run([SYSTEMCTL, "list-units", "--failed", "--all", "--plain", "--no-legend", "--no-pager"])
        if out.returncode != 0:
            raise RuntimeError(f"systemctl list-units failed: {out.stderr.strip()[:200]}")
        for unit in _units_from_list(out.stdout):
            if any(fnmatch.fnmatch(unit, pat) for pat in ignore):
                continue
            failed.add(unit)
            results.append(Result(NAME, f"systemd.unit:{unit}", CRITICAL, f"{unit} has failed"))

    optional = cfg.get("optional", [])
    watch = list(cfg.get("watch", []))
    units, unmatched = expand(host, watch)
    for pattern in unmatched:
        if not any(fnmatch.fnmatch(pattern, o) or pattern == o for o in optional):
            results.append(Result(NAME, f"systemd.watch:{pattern}", WARNING, f"no unit matches {pattern}"))

    if units:
        props = "Id,LoadState,ActiveState,SubState,NRestarts,Result"
        out = host.run([SYSTEMCTL, "show", "--no-pager", "-p", props, *units])
        shown = parse_show(out.stdout)
        inactive_sev = parse_severity(cfg.get("inactive_severity", "critical"))
        restarts_prev: dict = data.setdefault("restarts", {})
        for unit in units:
            info = shown.get(unit)
            is_optional = any(fnmatch.fnmatch(unit, o) for o in optional)
            if info is None or info.get("LoadState") == "not-found":
                if not is_optional:
                    results.append(Result(NAME, f"systemd.unit:{unit}", WARNING, f"{unit} is not installed"))
                continue
            active = info.get("ActiveState", "")
            sub = info.get("SubState", "")
            if unit not in failed and active not in ("active", "reloading", "activating"):
                results.append(Result(NAME, f"systemd.unit:{unit}", inactive_sev, f"{unit} is {active} ({sub})"))
            if "NRestarts" in info:
                try:
                    now = int(info["NRestarts"])
                except ValueError:
                    continue
                prev = restarts_prev.get(unit)
                restarts_prev[unit] = now
                if prev is None:
                    continue
                delta = now - prev if now >= prev else now
                sev = grade(delta, cfg.get("restart_warn", 1), cfg.get("restart_crit", 5))
                if delta > 0 and sev > OK:
                    times = "once" if delta == 1 else f"{delta} times"
                    results.append(Result(NAME, f"systemd.restarts:{unit}", sev,
                                          f"{unit} was restarted {times} by systemd since the last check",
                                          detail=f"restart counter {now}", event=True))
    return results
