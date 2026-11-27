"""OOM kills: kernel log entries "Killed process N (name)" since the last run."""

from __future__ import annotations

import re
from collections import Counter

from ..model import Result, parse_severity

NAME = "oom"
JOURNALCTL = "/usr/bin/journalctl"
KILLED = re.compile(r"Killed process (\d+) \(([^)]*)\)")
# Older kernels and some cgroup paths log "Memory cgroup out of memory: Killed process ..." which the
# same pattern covers; "oom_reaper: reaped process" lines repeat the same kill and are not counted.


def parse(stdout: str, after: float) -> tuple[Counter, float]:
    """Count kills per process name in ``journalctl -o short-unix`` output newer than ``after``."""
    kills: Counter = Counter()
    newest = after
    for line in stdout.splitlines():
        stamp, _, rest = line.partition(" ")
        try:
            ts = float(stamp)
        except ValueError:
            continue
        if ts <= after:
            continue
        m = KILLED.search(rest)
        if m:
            kills[m.group(2)] += 1
            newest = max(newest, ts)
    return kills, newest


def run(cfg: dict, host, data: dict) -> list[Result]:
    now = host.now()
    last = data.get("last_ts")
    since = last if last is not None else now - 60 * float(cfg.get("lookback_minutes", 60))
    out = host.run([JOURNALCTL, "-k", "--no-pager", "-q", "-o", "short-unix", "--since", f"@{int(since)}",
                    "-g", "Killed process"], timeout=60)
    if out.returncode not in (0, 1):
        raise RuntimeError(f"journalctl failed: {out.stderr.strip()[:200]}")
    kills, newest = parse(out.stdout, since)
    # Remember the newest entry handled; without entries, move on to "now" so the window stays short.
    data["last_ts"] = max(newest, now - 5) if kills else now - 5
    if not kills:
        return []
    names = ", ".join(f"{name} ({n}x)" if n > 1 else name for name, n in kills.most_common())
    total = sum(kills.values())
    word = "process" if total == 1 else "processes"
    return [Result(NAME, "oom.kernel", parse_severity(cfg.get("severity", "critical")),
                   f"OOM killer stopped {total} {word}: {names}",
                   detail="the kernel ran out of memory; check `journalctl -k -g 'Out of memory'`",
                   event=True)]
