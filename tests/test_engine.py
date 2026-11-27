"""The alert engine: new, confirm, cooldown, reminder, escalation, events, recoveries, state file."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from fakes import NOW, FakeHost

from lumen_watchdog import engine
from lumen_watchdog.config import load
from lumen_watchdog.model import CRITICAL, ESCALATED, EVENT, NEW, RECOVERED, REMINDER, WARNING, Result

ONLY_LOAD = """
[watchdog]
host_name = "host.example.com"
[defaults]
repeat_minutes = 60
cooldown_minutes = 15
confirm_runs = 1
[checks.systemd]
enabled = false
[checks.oom]
enabled = false
[checks.mail]
enabled = false
[checks.disk]
enabled = false
[checks.memory]
enabled = false
[checks.tls]
enabled = false
[checks.http]
enabled = false
[checks.backups]
enabled = false
[checks.apt]
enabled = false
[checks.quota]
enabled = false
"""


class Scripted:
    """Replaces one check with a list of result lists, one per run."""

    def __init__(self, *runs):
        self.runs = list(runs)

    def __call__(self, cfg, host, data):
        item = self.runs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def res(sev=WARNING, key="load.5min", summary="load high", event=False):
    return Result("load", key, sev, summary, event=event)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load(None, ONLY_LOAD)
        self.state = engine.empty_state()
        self.host = FakeHost()

    def step(self, script_item, now, extra=""):
        cfg = load(None, ONLY_LOAD + extra) if extra else self.cfg
        with mock.patch.dict(engine.check_registry.REGISTRY, {"load": Scripted(script_item)}):
            alert, _ = engine.evaluate(cfg, self.host, self.state, now=now)
        return [(n.kind, n.result.key, n.result.severity) for n in alert.notices], alert

    def test_lifecycle_new_quiet_reminder_escalate_recover(self):
        self.assertEqual(self.step([res()], NOW)[0], [(NEW, "load.5min", WARNING)])
        self.assertEqual(self.step([res()], NOW + 300)[0], [])
        self.assertEqual(self.step([res()], NOW + 3600)[0], [(REMINDER, "load.5min", WARNING)])
        self.assertEqual(self.step([res(CRITICAL)], NOW + 3900)[0], [(ESCALATED, "load.5min", CRITICAL)])
        notices, alert = self.step([], NOW + 4200)
        self.assertEqual(notices, [(RECOVERED, "load.5min", CRITICAL)])
        self.assertEqual(alert.notices[0].since, NOW)
        self.assertEqual(alert.open_problems, 0)
        self.assertEqual(self.state["problems"], {})

    def test_confirm_runs_delays_the_first_alert(self):
        extra = "[checks.load]\nconfirm_runs = 3\n"
        self.assertEqual(self.step([res()], NOW, extra)[0], [])
        self.assertEqual(self.step([res()], NOW + 300, extra)[0], [])
        self.assertEqual(self.step([res()], NOW + 600, extra)[0], [(NEW, "load.5min", WARNING)])

    def test_unconfirmed_problem_recovers_silently(self):
        extra = "[checks.load]\nconfirm_runs = 2\n"
        self.step([res()], NOW, extra)
        self.assertEqual(self.step([], NOW + 300, extra)[0], [])

    def test_cooldown_holds_back_a_flapping_problem(self):
        self.step([res()], NOW)
        self.assertEqual(self.step([], NOW + 300)[0], [(RECOVERED, "load.5min", WARNING)])
        self.assertEqual(self.step([res()], NOW + 600)[0], [])            # within 15 min of the last alert
        self.assertEqual(self.step([res()], NOW + 900)[0], [(NEW, "load.5min", WARNING)])

    def test_events_are_sent_every_time_and_never_recover(self):
        ev = res(CRITICAL, "oom.kernel", "OOM killer stopped 1 process: clamd", event=True)
        self.assertEqual(self.step([ev], NOW)[0], [(EVENT, "oom.kernel", CRITICAL)])
        self.assertEqual(self.step([ev], NOW + 300)[0], [(EVENT, "oom.kernel", CRITICAL)])
        self.assertEqual(self.step([], NOW + 600)[0], [])
        self.assertEqual(self.state["problems"], {})

    def test_broken_check_becomes_a_warning(self):
        notices, alert = self.step(RuntimeError("boom"), NOW)
        self.assertEqual(notices, [(NEW, "load.error", WARNING)])
        self.assertEqual(alert.notices[0].result.detail, "RuntimeError: boom")

    def test_every_minutes_reuses_cached_results(self):
        extra = "[checks.load]\nevery_minutes = 60\n"
        self.step([res()], NOW, extra)
        # Not due: the scripted check would raise IndexError if it were called.
        with mock.patch.dict(engine.check_registry.REGISTRY, {"load": Scripted()}):
            alert, per_check = engine.evaluate(load(None, ONLY_LOAD + extra), self.host, self.state, now=NOW + 600)
        self.assertEqual([r.key for r in per_check["load"]], ["load.5min"])
        self.assertEqual(alert.notices, [])
        self.assertEqual(self.state["problems"]["load.5min"]["fails"], 1)   # cached runs do not count

    def test_disabled_check_drops_its_problems_without_a_message(self):
        self.step([res()], NOW)
        cfg = load(None, ONLY_LOAD.replace("[checks.quota]", "[checks.load]\nenabled = false\n[checks.quota]"))
        alert, _ = engine.evaluate(cfg, self.host, self.state, now=NOW + 300)
        self.assertEqual(alert.notices, [])
        self.assertEqual(self.state["problems"], {})

    def test_recoveries_can_be_switched_off(self):
        extra = "[checks.load]\nrecoveries = false\n"
        self.step([res()], NOW, extra)
        self.assertEqual(self.step([], NOW + 300, extra)[0], [])

    def test_state_file_round_trip_and_damage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "state.json")
            self.step([res()], NOW)
            engine.save_state(path, self.state)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(engine.load_state(path)["problems"]["load.5min"]["since"], NOW)
            with open(path, "w") as fh:
                fh.write("{not json")
            self.assertEqual(engine.load_state(path), engine.empty_state())
            with open(path, "w") as fh:
                json.dump({"version": 99}, fh)
            self.assertEqual(engine.load_state(path), engine.empty_state())
            self.assertEqual(engine.load_state(os.path.join(tmp, "none.json")), engine.empty_state())


if __name__ == "__main__":
    unittest.main()
