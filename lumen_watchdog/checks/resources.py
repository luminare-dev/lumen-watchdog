"""Disk space and inodes per mount, memory and swap pressure, load per CPU."""

from __future__ import annotations

import fnmatch

from ..model import Result, grade
from .util import size_text


def mounts_from_proc(text: str, fstypes: list[str]) -> list[str]:
    seen: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        point = parts[1].replace("\\040", " ")
        if parts[2] in fstypes and point not in seen:
            seen.append(point)
    return seen


def run_disk(cfg: dict, host, data: dict) -> list[Result]:
    mounts = list(cfg.get("mounts") or [])
    if not mounts:
        mounts = mounts_from_proc(host.read_text("/proc/mounts") or "", cfg.get("fstypes", []))
    results = []
    for mount in mounts:
        if any(fnmatch.fnmatch(mount, pat) for pat in cfg.get("ignore", [])):
            continue
        try:
            st = host.statvfs(mount)
        except OSError as exc:
            results.append(Result("disk", f"disk.space:{mount}", 2, f"{mount} cannot be read",
                                  detail=exc.strerror or str(exc)))
            continue
        if st.total > 0:
            used = 100.0 * (st.total - st.free) / st.total
            sev = grade(used, cfg.get("used_warn_percent"), cfg.get("used_crit_percent"))
            if sev:
                results.append(Result("disk", f"disk.space:{mount}", sev,
                                      f"{mount} is {used:.0f}% full ({size_text(st.free)} free)"))
        if st.inodes_total > 0:
            used = 100.0 * (st.inodes_total - st.inodes_free) / st.inodes_total
            sev = grade(used, cfg.get("inodes_warn_percent"), cfg.get("inodes_crit_percent"))
            if sev:
                results.append(Result("disk", f"disk.inodes:{mount}", sev,
                                      f"{mount} uses {used:.0f}% of its inodes ({st.inodes_free} free)"))
    return results


def parse_meminfo(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[key] = int(parts[0]) * 1024
    return out


def parse_pressure(text: str) -> dict[str, float]:
    """``/proc/pressure/memory`` -> {"some": avg60, "full": avg60}."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        fields = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        try:
            out[parts[0]] = float(fields.get("avg60", "0"))
        except ValueError:
            pass
    return out


def run_memory(cfg: dict, host, data: dict) -> list[Result]:
    info = parse_meminfo(host.read_text("/proc/meminfo") or "")
    results = []
    total = info.get("MemTotal", 0)
    if total:
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        pct = 100.0 * avail / total
        sev = grade(pct, cfg.get("available_warn_percent"), cfg.get("available_crit_percent"), below=True)
        if sev:
            results.append(Result("memory", "memory.available", sev,
                                  f"only {size_text(avail)} memory available ({pct:.0f}% of {size_text(total)})"))
    swap_total = info.get("SwapTotal", 0)
    if swap_total:
        used = swap_total - info.get("SwapFree", 0)
        pct = 100.0 * used / swap_total
        sev = grade(pct, cfg.get("swap_warn_percent"), cfg.get("swap_crit_percent"))
        if sev:
            results.append(Result("memory", "memory.swap", sev,
                                  f"swap is {pct:.0f}% used ({size_text(used)} of {size_text(swap_total)})"))
    pressure = parse_pressure(host.read_text("/proc/pressure/memory") or "")
    if "full" in pressure:
        full = pressure["full"]
        sev = grade(full, cfg.get("pressure_warn"), cfg.get("pressure_crit"))
        if sev:
            results.append(Result("memory", "memory.pressure", sev,
                                  f"memory pressure: tasks stalled {full:.1f}% of the last minute"))
    return results


def run_load(cfg: dict, host, data: dict) -> list[Result]:
    text = host.read_text("/proc/loadavg") or ""
    parts = text.split()
    if len(parts) < 3:
        return []
    load5 = float(parts[1])
    cpus = max(1, host.cpu_count())
    per_cpu = load5 / cpus
    sev = grade(per_cpu, cfg.get("per_cpu_warn"), cfg.get("per_cpu_crit"))
    if not sev:
        return []
    return [Result("load", "load.5min", sev, f"load {load5:.2f} over 5 min on {cpus} CPUs ({per_cpu:.1f} per CPU)")]
