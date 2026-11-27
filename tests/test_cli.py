"""Configuration loading, the env file and the command line (dry run, test, run, notify-failure)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from fakes import FakeHost, ok

from lumen_watchdog import cli, engine
from lumen_watchdog.config import ConfigError, check_settings, load, load_env_file
from lumen_watchdog.notify import discord

ROOT = Path(__file__).resolve().parent.parent
WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/" + "z" * 68
ENV = {"DISCORD_WEBHOOK_URL": WEBHOOK, "DISCORD_PING_USER_ID": "123456789012345678"}


class ConfigTest(unittest.TestCase):
    def test_example_config_is_valid(self):
        cfg = load(str(ROOT / "examples" / "config.toml"))
        self.assertTrue(cfg["checks"]["systemd"]["watch"])

    def test_defaults_and_overrides(self):
        cfg = load(None, "[defaults]\nrepeat_minutes = 30\n[checks.apt]\nconfirm_runs = 2\n")
        self.assertEqual(check_settings(cfg, "load")["repeat_minutes"], 30)
        self.assertEqual(check_settings(cfg, "apt")["repeat_minutes"], 1440)
        self.assertEqual(check_settings(cfg, "apt")["confirm_runs"], 2)
        self.assertEqual(check_settings(cfg, "apt")["every_minutes"], 360)

    def test_errors(self):
        bad = [
            "[checks.nope]\nenabled = true\n",
            "[checks.load]\nper_cpu = 1\n",
            '[discord]\nmin_severity = "loud"\n',
            "[ntfy]\nenabled = true\n",
            '[[discord.buttons]]\nlabel = "x"\nurl = "javascript:alert(1)"\n',
            "[[checks.mail.endpoints]]\nname = \"x\"\n",
            '[checks.apt]\nreboot_required = "maybe"\n',
        ]
        for text in bad:
            with self.subTest(text=text), self.assertRaises((ConfigError, ValueError)):
                load(None, text)

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load("/nonexistent/config.toml")


class EnvFileTest(unittest.TestCase):
    def test_reads_values_and_keeps_existing_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "secrets.env")
            Path(path).write_text("# comment\nexport A=1\nB='two'\nC=\"three\"\nDISCORD_WEBHOOK_URL=new\n")
            os.chmod(path, 0o600)
            env = {"DISCORD_WEBHOOK_URL": "kept"}
            self.assertEqual(load_env_file(path, env), [])
            self.assertEqual(env, {"A": "1", "B": "two", "C": "three", "DISCORD_WEBHOOK_URL": "kept"})

    def test_refuses_a_readable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "secrets.env")
            Path(path).write_text("A=1\n")
            os.chmod(path, 0o644)
            env: dict = {}
            warnings = load_env_file(path, env)
            self.assertEqual(env, {})
            self.assertIn("mode 600", warnings[0])


def quiet_config(tmp: str, extra: str = "") -> str:
    disabled = "\n".join(f"[checks.{c}]\nenabled = false" for c in
                         ("systemd", "oom", "mail", "disk", "tls", "http", "backups", "apt", "quota"))
    path = os.path.join(tmp, "config.toml")
    Path(path).write_text(f"""
[watchdog]
host_name = "web.example.com"
state_file = "{tmp}/state.json"
env_file = ""
timezone = "UTC"
{disabled}
[[discord.buttons]]
label = "Panel"
url = "https://panel.example.com/"
{extra}
""")
    return path


def run_cli(argv, host, env) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = cli.main(argv, host=host, environ=env)
    return rc, out.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = self.tmp_dir.name
        self.host = FakeHost()
        self.host.files["/proc/meminfo"] = "MemTotal: 4000000 kB\nMemAvailable: 100000 kB\n"
        self.host.files["/proc/loadavg"] = "0.1 0.1 0.1 1/1 1\n"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_dry_run_prints_results_and_payloads_and_saves_nothing(self):
        rc, out = run_cli(["-c", quiet_config(self.tmp), "run", "--dry-run"], self.host, dict(ENV))
        self.assertEqual(rc, 0)
        self.assertIn("[critical] memory: only 97.7 MiB memory available", out)
        self.assertIn("[ok]       load", out)
        self.assertIn("--- discord payload ---", out)
        self.assertIn('"flags": 32768', out)
        self.assertNotIn(WEBHOOK, out)
        self.assertNotIn("z" * 20, out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "state.json")))
        self.assertFalse(any(c[0] == "http" for c in self.host.calls))

    def test_run_sends_once_saves_state_and_stays_quiet_next_time(self):
        self.host.answer(discord.execute_url(WEBHOOK), ok(b'{"id": "1"}'))
        config = quiet_config(self.tmp)
        rc, out = run_cli(["-c", config, "run"], self.host, dict(ENV))
        self.assertEqual(rc, 0, out)
        self.assertIn("discord: sent (message 1)", out)
        state = json.loads(Path(self.tmp, "state.json").read_text())
        self.assertIn("memory.available", state["problems"])
        self.host._now += 300
        rc, out = run_cli(["-c", config, "run"], self.host, dict(ENV))
        self.assertIn("0 notices", out)
        posts = [c for c in self.host.calls if c[0] == "http"]
        self.assertEqual(len(posts), 1)

    def test_failed_delivery_returns_1(self):
        rc, out = run_cli(["-c", quiet_config(self.tmp), "run"], self.host, {})
        self.assertEqual(rc, 1)
        self.assertIn("DISCORD_WEBHOOK_URL is not set", out)

    def test_test_command_dry_run_and_send(self):
        config = quiet_config(self.tmp)
        rc, out = run_cli(["-c", config, "test", "--channel", "discord", "--dry-run"], self.host, dict(ENV))
        self.assertEqual(rc, 0)
        self.assertIn("Test message", out)
        self.host.answer(discord.execute_url(WEBHOOK), ok(b'{"id": "7"}'))
        rc, out = run_cli(["-c", config, "test", "--channel", "discord"], self.host, dict(ENV))
        self.assertEqual((rc, out.strip()), (0, "discord: sent (message 7)"))

    def test_test_command_enables_ntfy_when_named(self):
        config = quiet_config(self.tmp, '[ntfy]\nurl = "https://ntfy.example.com"\ntopic = "t"\n')
        self.host.answer("https://ntfy.example.com", ok(b'{"id": "n1"}'))
        rc, out = run_cli(["-c", config, "test", "--channel", "ntfy"], self.host, dict(ENV))
        self.assertEqual((rc, out.strip()), (0, "ntfy: sent (message n1)"))

    def test_notify_failure_includes_the_journal_tail(self):
        self.host.on(["/usr/bin/journalctl", "-u", "lumen-watchdog.service", "-n", "8", "-o", "cat", "--no-pager"],
                     "Traceback (most recent call last):\nKeyError: 'x'\n")
        rc, out = run_cli(["-c", quiet_config(self.tmp), "notify-failure", "--dry-run"], self.host, dict(ENV))
        self.assertEqual(rc, 0)
        self.assertIn("KeyError", out)
        self.assertIn("Watchdog run failed", out)

    def test_check_config_and_list(self):
        rc, out = run_cli(["-c", quiet_config(self.tmp), "check-config"], self.host, {})
        self.assertEqual(rc, 0)
        self.assertIn("channels: discord", out)
        rc, out = run_cli(["list-checks"], self.host, {})
        self.assertEqual(len(out.strip().splitlines()), 11)
        rc, out = run_cli(["-c", "/nonexistent.toml", "run"], self.host, {})
        self.assertEqual(rc, 2)

    def test_lock_skips_a_parallel_run(self):
        import fcntl
        config = quiet_config(self.tmp)
        with open(os.path.join(self.tmp, "state.json.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rc, out = run_cli(["-c", config, "run"], self.host, dict(ENV))
        self.assertEqual((rc, out.strip()), (0, "another run is in progress; skipped"))
        self.assertEqual(engine.load_state(os.path.join(self.tmp, "state.json")), engine.empty_state())


if __name__ == "__main__":
    unittest.main()
