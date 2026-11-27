"""Runs the checks and decides what to send: new problems, escalations, reminders, events, recoveries.

State (JSON, one file) keeps per check the time of the last run, its cached results and its private
data (journal position, restart counters), and per open problem when it started, how many runs in a
row it failed and when it was last sent.
"""

from __future__ import annotations

import json
import os
import tempfile
import traceback
from dataclasses import asdict

from . import checks as check_registry
from .config import CHECK_NAMES, check_settings
from .model import (ESCALATED, EVENT, NEW, OK, RECOVERED, REMINDER, WARNING, Alert, Notice, Result)

STATE_VERSION = 1
TOLERANCE = 30  # seconds: a timer that fires a little early still counts as due


def empty_state() -> dict:
    return {"version": STATE_VERSION, "checks": {}, "problems": {}, "last_alert": {}}


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except FileNotFoundError:
        return empty_state()
    except (OSError, json.JSONDecodeError):
        # A damaged state file must never stop the watchdog: start over (open problems alert again).
        return empty_state()
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        return empty_state()
    for key, default in empty_state().items():
        state.setdefault(key, default)
    return state


def save_state(path: str, state: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _result_from(d: dict) -> Result:
    return Result(d["check"], d["key"], d["severity"], d["summary"], d.get("detail", ""), d.get("event", False))


def collect(cfg: dict, host, state: dict, now: float, only: list[str] | None = None,
            force: bool = False) -> tuple[dict[str, list[Result]], set[str]]:
    """Run the due checks. Returns (results per enabled check, names of the checks that ran now)."""
    per_check: dict[str, list[Result]] = {}
    ran: set[str] = set()
    for name in CHECK_NAMES:
        ccfg = cfg["checks"][name]
        if not ccfg.get("enabled", True) or (only and name not in only):
            continue
        settings = check_settings(cfg, name)
        cstate = state["checks"].setdefault(name, {"last_run": 0, "results": [], "data": {}})
        every = 60 * float(settings.get("every_minutes") or 0)
        due = force or every <= 0 or now - cstate.get("last_run", 0) >= every - TOLERANCE
        if due:
            try:
                results = check_registry.REGISTRY[name](ccfg, host, cstate.setdefault("data", {}))
            except Exception as exc:  # noqa: BLE001 - one broken check must not stop the others
                detail = traceback.format_exception_only(type(exc), exc)[-1].strip()
                results = [Result(name, f"{name}.error", WARNING, f"the {name} check failed", detail=detail[:300])]
            cstate["last_run"] = now
            cstate["results"] = [asdict(r) for r in results if not r.event]
            ran.add(name)
        else:
            results = [_result_from(d) for d in cstate.get("results", [])]
        per_check[name] = results
    return per_check, ran


def evaluate(cfg: dict, host, state: dict, now: float | None = None, only: list[str] | None = None,
             force: bool = False) -> tuple[Alert, dict[str, list[Result]]]:
    """Run the checks, update ``state`` in place and return the alert to send (maybe empty)."""
    now = host.now() if now is None else now
    per_check, ran = collect(cfg, host, state, now, only, force)
    alert = Alert(host=cfg["watchdog"]["host_name"] or host.hostname(), created=now)
    problems: dict = state["problems"]
    last_alert: dict = state["last_alert"]
    seen: set[str] = set()

    for name, results in per_check.items():
        settings = check_settings(cfg, name)
        confirm = max(1, int(settings.get("confirm_runs") or 1))
        repeat = 60 * float(settings.get("repeat_minutes") or 0)
        cooldown = 60 * float(settings.get("cooldown_minutes") or 0)
        for r in results:
            if r.severity <= OK:
                continue
            if r.event:
                if name in ran:
                    alert.notices.append(Notice(EVENT, r, now))
                    last_alert[r.key] = now
                continue
            seen.add(r.key)
            p = problems.get(r.key)
            if p is None:
                p = problems[r.key] = {"check": name, "since": now, "fails": 0, "alerted": False,
                                       "alerted_severity": 0, "last_alert": 0}
            if name in ran:
                p["fails"] += 1
            p.update(severity=r.severity, summary=r.summary, detail=r.detail)
            if p["fails"] < confirm:
                continue
            kind = None
            if not p["alerted"]:
                if now - last_alert.get(r.key, 0) >= cooldown:
                    kind = NEW
            elif r.severity > p["alerted_severity"]:
                kind = ESCALATED
            elif repeat > 0 and now - p["last_alert"] >= repeat - TOLERANCE:
                kind = REMINDER
            if r.severity < p["alerted_severity"]:
                p["alerted_severity"] = r.severity
            if kind:
                alert.notices.append(Notice(kind, r, p["since"]))
                p.update(alerted=True, alerted_severity=r.severity, last_alert=now)
                last_alert[r.key] = now

    for key in list(problems):
        p = problems[key]
        if key in seen:
            continue
        check = p.get("check", "")
        if check in per_check or check not in CHECK_NAMES or not cfg["checks"].get(check, {}).get("enabled", True):
            if p.get("alerted") and check in per_check and check_settings(cfg, check).get("recoveries", True):
                r = Result(check, key, p.get("alerted_severity", WARNING), p.get("summary", key))
                alert.notices.append(Notice(RECOVERED, r, p.get("since")))
            del problems[key]

    # Old cooldown entries are only needed for a while.
    for key in [k for k, t in last_alert.items() if now - t > 7 * 86400]:
        del last_alert[key]

    alert.open_problems = len(problems)
    order = {k: i for i, k in enumerate((NEW, ESCALATED, EVENT, REMINDER, RECOVERED))}
    alert.notices.sort(key=lambda n: (order[n.kind], -n.result.severity, n.result.key))
    return alert, per_check
