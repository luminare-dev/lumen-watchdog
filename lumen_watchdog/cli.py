"""Command line: run (default), test, notify-failure, check-config, list-checks."""

from __future__ import annotations

import argparse
import copy
import fcntl
import os
import sys

from . import __version__, engine
from .checks import DESCRIPTIONS
from .config import CHECK_NAMES, ConfigError, load, load_env_file
from .host import Host
from .model import OK, Alert, severity_name
from .notify import CHANNELS, deliver, dumps, enabled_channels, render

DEFAULT_CONFIG = "/etc/lumen-watchdog/config.toml"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lumen-watchdog", description="Small health watchdog with Discord and ntfy alerts.")
    p.add_argument("--version", action="version", version=f"lumen-watchdog {__version__}")
    p.add_argument("-c", "--config", default=os.environ.get("LUMEN_WATCHDOG_CONFIG", DEFAULT_CONFIG))
    p.add_argument("--state", help="state file (default: watchdog.state_file from the configuration)")
    sub = p.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run the due checks and send alerts (the timer's command)")
    run.add_argument("--dry-run", action="store_true",
                     help="run every check now, print the results and the payloads; send and save nothing")
    run.add_argument("--only", action="append", choices=CHECK_NAMES, help="run only this check (repeatable)")
    run.add_argument("--force", action="store_true", help="run every check now, ignoring every_minutes")

    test = sub.add_parser("test", help="send a test message")
    test.add_argument("--channel", choices=(*CHANNELS, "all"), default="all")
    test.add_argument("--dry-run", action="store_true", help="print the payload instead of sending it")

    fail = sub.add_parser("notify-failure", help="tell the channels that the watchdog run failed (OnFailure unit)")
    fail.add_argument("--unit", default="lumen-watchdog.service")
    fail.add_argument("--dry-run", action="store_true")

    sub.add_parser("check-config", help="validate the configuration")
    sub.add_parser("list-checks", help="list the checks")
    return p


def main(argv: list[str] | None = None, host: Host | None = None, environ=None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "run"
    if command == "list-checks":
        for name in CHECK_NAMES:
            print(f"{name:8} {DESCRIPTIONS[name]}")
        return 0
    try:
        cfg = load(args.config)
    except (ConfigError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if command == "check-config":
        print(f"ok: {args.config}; channels: {', '.join(enabled_channels(cfg)) or 'none'}")
        return 0

    environ = os.environ if environ is None else environ
    for warning in load_env_file(cfg["watchdog"].get("env_file", ""), environ):
        print(f"warning: {warning}", file=sys.stderr)
    host = host or Host()

    if command == "test":
        alert = Alert(cfg["watchdog"]["host_name"] or host.hostname(), test=True, created=host.now())
        channels = list(CHANNELS) if args.channel == "all" else [args.channel]
        return _send_or_print(host, alert, cfg, channels, args.dry_run, environ, explicit=args.channel != "all")
    if command == "notify-failure":
        out = host.run(["/usr/bin/journalctl", "-u", args.unit, "-n", "8", "-o", "cat", "--no-pager"])
        alert = Alert(cfg["watchdog"]["host_name"] or host.hostname(), created=host.now(),
                      failure=f"{args.unit} failed.\n{out.stdout.strip()[-500:]}")
        return _send_or_print(host, alert, cfg, enabled_channels(cfg), args.dry_run, environ)
    return _run(args, cfg, host, environ)


def _send_or_print(host, alert, cfg, channels, dry_run, environ, explicit=False) -> int:
    if dry_run:
        for channel in channels:
            print(f"--- {channel} payload ---")
            print(dumps(render(channel, alert, cfg, environ)))
        return 0
    if explicit:
        cfg = copy.deepcopy(cfg)
        for c in channels:
            cfg[c]["enabled"] = True
    else:
        channels = [c for c in channels if cfg[c].get("enabled")]
    status = deliver(host, alert, cfg, channels, environ)
    for channel, text in status.items():
        print(f"{channel}: {text}")
    return 1 if any(t.startswith("error") for t in status.values()) else 0


def _run(args, cfg, host, environ) -> int:
    state_path = args.state or cfg["watchdog"]["state_file"]
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        # Read-only: work on a copy of the saved state, run every check now, save nothing.
        state = copy.deepcopy(engine.load_state(state_path))
        alert, per_check = engine.evaluate(cfg, host, state, only=getattr(args, "only", None), force=True)
        _print_results(per_check)
        channels = enabled_channels(cfg)
        if not alert.notices:
            print("\nnothing to send")
        for channel in channels:
            print(f"\n--- {channel} payload ---")
            print(dumps(render(channel, alert, cfg, environ)))
        return 0

    lock_path = state_path + ".lock"
    os.makedirs(os.path.dirname(lock_path) or ".", mode=0o700, exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another run is in progress; skipped")
            return 0
        state = engine.load_state(state_path)
        alert, per_check = engine.evaluate(cfg, host, state, only=getattr(args, "only", None),
                                           force=getattr(args, "force", False))
        problems = sum(1 for rs in per_check.values() for r in rs if r.severity > OK)
        print(f"{len(per_check)} checks, {problems} problems, {len(alert.notices)} notices")
        rc = 0
        if alert.notices:
            status = deliver(host, alert, cfg, enabled_channels(cfg), environ)
            for channel, text in status.items():
                print(f"{channel}: {text}")
            rc = 1 if any(t.startswith("error") for t in status.values()) else 0
        engine.save_state(state_path, state)
        return rc


def _print_results(per_check) -> None:
    for name, results in per_check.items():
        bad = [r for r in results if r.severity > OK]
        if not bad:
            print(f"[ok]       {name}")
            continue
        for r in bad:
            tag = f"[{severity_name(r.severity)}]"
            print(f"{tag:10} {name}: {r.summary}" + (f"  ({r.detail})" if r.detail else ""))
