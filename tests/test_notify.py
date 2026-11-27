"""Discord and ntfy payloads (snapshots), delivery with rate limits, and secrets staying out of output.

Refresh the snapshots after an intended change with:
    LUMEN_WATCHDOG_UPDATE_SNAPSHOTS=1 python3 -m unittest discover -s tests -p test_notify.py
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from fakes import NOW, FakeHost, ok

from lumen_watchdog.config import load
from lumen_watchdog.host import HttpResponse
from lumen_watchdog.model import (CRITICAL, ESCALATED, EVENT, NEW, RECOVERED, REMINDER, WARNING, Alert, Notice,
                                  Result)
from lumen_watchdog.notify import deliver, discord, ntfy, render
from lumen_watchdog.notify.common import SendError, for_channel, post_json

SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"
WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/" + "x" * 68
PING = "123456789012345678"
ENV = {"DISCORD_WEBHOOK_URL": WEBHOOK, "DISCORD_PING_USER_ID": PING, "NTFY_TOKEN": "tk_" + "y" * 29}

CONFIG = """
[watchdog]
host_name = "web.example.com"
interval_minutes = 5
timezone = "UTC"
[discord]
min_severity = "warning"
[[discord.buttons]]
label = "Panel"
url = "https://panel.example.com/"
[[discord.buttons]]
label = "Webmail"
url = "https://webmail.example.com/"
[ntfy]
enabled = true
url = "https://ntfy.example.com"
topic = "watchdog"
click = "https://panel.example.com/"
min_severity = "critical"
[[ntfy.actions]]
label = "Panel"
url = "https://panel.example.com/"
"""


def sample_alert() -> Alert:
    def r(check, key, sev, summary, detail=""):
        return Result(check, key, sev, summary, detail)
    return Alert("web.example.com", [
        Notice(NEW, r("systemd", "systemd.unit:dovecot.service", CRITICAL, "dovecot.service has failed"), NOW),
        Notice(NEW, r("mail", "mail.queue", WARNING, "Postfix queue holds 64 messages (deferred 64)"), NOW),
        Notice(ESCALATED, r("disk", "disk.space:/", CRITICAL, "/ is 96% full (3.0 GiB free)"), NOW - 7200),
        Notice(EVENT, r("oom", "oom.kernel", CRITICAL, "OOM killer stopped 1 process: clamd",
                        "the kernel ran out of memory"), NOW),
        Notice(REMINDER, r("tls", "tls:web.example.com", WARNING, "web.example.com: certificate expires in 12 days",
                           "issuer CN=R11"), NOW - 86400),
        Notice(RECOVERED, r("http", "http:Webmail", CRITICAL, "Webmail: HTTP 502"), NOW - 900),
    ], open_problems=5, created=NOW)


def check_snapshot(test: unittest.TestCase, name: str, payload: dict) -> None:
    path = SNAPSHOTS / f"{name}.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if os.environ.get("LUMEN_WATCHDOG_UPDATE_SNAPSHOTS") == "1" or not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
    test.assertEqual(text, path.read_text(encoding="utf-8"), f"snapshot {name} changed")


def count_components(components: list) -> int:
    total = 0
    for c in components:
        total += 1
        total += count_components(c.get("components", []))
        if "accessory" in c:
            total += 1
    return total


def text_length(components: list) -> int:
    n = 0
    for c in components:
        if c.get("type") == 10:
            n += len(c["content"])
        n += text_length(c.get("components", []))
    return n


class DiscordPayloadTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load(None, CONFIG)

    def test_snapshot_alert(self):
        payload = render("discord", sample_alert(), self.cfg, ENV)
        check_snapshot(self, "discord-alert", payload)

    def test_structure(self):
        payload = render("discord", sample_alert(), self.cfg, ENV)
        self.assertEqual(payload["flags"], 1 << 15)
        self.assertNotIn("content", payload)
        self.assertNotIn("embeds", payload)
        self.assertEqual(payload["username"], "Lumen Watchdog")
        self.assertTrue(payload["avatar_url"].startswith("https://raw.githubusercontent.com/luminare-dev/"))
        self.assertEqual(payload["allowed_mentions"], {"parse": [], "users": [PING]})
        top, container = payload["components"]
        self.assertEqual(top, {"type": 10, "content": f"<@{PING}> Critical on web.example.com: 2 new problems, "
                                                     "1 got worse, 1 event, 1 still open, 1 recovered"})
        self.assertEqual(container["type"], 17)
        self.assertEqual(container["accent_color"], 0xFF5147)
        section = container["components"][0]
        self.assertEqual(section["type"], 9)
        self.assertEqual(section["accessory"]["type"], 11)
        types = [c["type"] for c in container["components"]]
        self.assertEqual(types[1], 14)
        self.assertEqual(types[-1], 1)
        buttons = container["components"][-1]["components"]
        self.assertEqual([(b["type"], b["style"], b["label"]) for b in buttons], [(2, 5, "Panel"), (2, 5, "Webmail")])

    def test_recovery_only_is_green_and_still_pings(self):
        alert = Alert("web.example.com", [Notice(RECOVERED, Result("http", "http:A", WARNING, "A: HTTP 502"), NOW)],
                      created=NOW)
        payload = render("discord", alert, self.cfg, ENV)
        self.assertEqual(payload["components"][1]["accent_color"], 0x58DD70)
        self.assertTrue(payload["components"][0]["content"].startswith(f"<@{PING}> Recovered on"))

    def test_test_message_snapshot(self):
        payload = render("discord", Alert("web.example.com", test=True, created=NOW), self.cfg, ENV)
        self.assertEqual(payload["components"][1]["accent_color"], 0x5BDFFF)
        check_snapshot(self, "discord-test", payload)

    def test_no_ping_without_a_valid_user_id(self):
        payload = render("discord", sample_alert(), self.cfg, {"DISCORD_PING_USER_ID": "@everyone"})
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(payload["components"][0]["type"], 17)
        self.assertNotIn("@everyone", json.dumps(payload))

    def test_limits_with_many_notices(self):
        notices = [Notice(NEW, Result("systemd", f"systemd.unit:u{i}", CRITICAL, f"unit-{i:03d}.service has failed "
                                      + "x" * 80, "detail " * 20), NOW) for i in range(200)]
        payload = render("discord", Alert("web.example.com", notices, 200, created=NOW), self.cfg, ENV)
        self.assertLessEqual(count_components(payload["components"]), 40)
        self.assertLessEqual(text_length(payload["components"]), 4000)
        self.assertIn("more", json.dumps(payload))

    def test_execute_url_adds_components_flag(self):
        url = discord.execute_url(WEBHOOK)
        self.assertTrue(url.startswith(WEBHOOK + "?"))
        self.assertIn("with_components=true", url)
        self.assertIn("wait=true", url)
        self.assertIn("thread_id=5", discord.execute_url(WEBHOOK + "?thread_id=5"))


class NtfyPayloadTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load(None, CONFIG)

    def test_snapshot_alert_with_severity_filter(self):
        payload = render("ntfy", sample_alert(), self.cfg, ENV)
        check_snapshot(self, "ntfy-alert", payload)
        self.assertEqual(payload["priority"], 5)
        self.assertEqual(payload["tags"], ["rotating_light"])
        self.assertNotIn("Postfix queue", payload["message"])          # warning, below this channel's floor
        self.assertIn("Webmail: HTTP 502", payload["message"])          # recovery of a critical problem

    def test_warning_and_test_message(self):
        cfg = load(None, CONFIG.replace('min_severity = "critical"', 'min_severity = "warning"'))
        alert = Alert("web.example.com", [Notice(NEW, Result("load", "load.5min", WARNING, "load high"), NOW)],
                      1, created=NOW)
        payload = render("ntfy", alert, cfg, ENV)
        self.assertEqual((payload["priority"], payload["tags"]), (4, ["warning"]))
        test = render("ntfy", Alert("web.example.com", test=True, created=NOW), cfg, ENV)
        check_snapshot(self, "ntfy-test", test)
        self.assertEqual(test["actions"], [{"action": "view", "label": "Panel", "url": "https://panel.example.com/",
                                            "clear": False}])

    def test_long_message_is_cut(self):
        notices = [Notice(NEW, Result("x", f"k{i}", CRITICAL, "y" * 100), NOW) for i in range(100)]
        payload = render("ntfy", Alert("h", notices, 100, created=NOW), self.cfg, ENV)
        self.assertLessEqual(len(payload["message"].encode()), ntfy.MESSAGE_BUDGET + 4)
        self.assertTrue(payload["message"].endswith("…"))


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load(None, CONFIG)

    def test_rate_limit_waits_retry_after_then_sends(self):
        host = FakeHost()
        url = discord.execute_url(WEBHOOK)
        host.answer(url, HttpResponse(429, b'{"message": "You are being rate limited.", "retry_after": 1.5}', 5),
                    ok(b'{"id": "42"}'))
        status = deliver(host, sample_alert(), self.cfg, ["discord"], ENV)
        self.assertEqual(status, {"discord": "sent (message 42)"})
        self.assertEqual(host.slept, [1.75])
        sent = json.loads(host.calls[-1][4])
        self.assertEqual(sent["flags"], 1 << 15)

    def test_retry_after_header_and_give_up_on_long_waits(self):
        host = FakeHost()
        host.answer("https://x.example.com/", HttpResponse(429, b"", 5, headers={"retry-after": "120"}))
        with self.assertRaises(SendError) as ctx:
            post_json(host, "https://x.example.com/", {}, what="discord")
        self.assertIn("longer than 60 s", str(ctx.exception))

    def test_server_errors_back_off_and_report_without_the_url(self):
        host = FakeHost()
        host.answer(discord.execute_url(WEBHOOK), HttpResponse(502, b"bad gateway", 5))
        status = deliver(host, sample_alert(), self.cfg, ["discord"], ENV)
        self.assertTrue(status["discord"].startswith("error: discord: gave up after 4 attempts"))
        self.assertEqual(host.slept, [2, 4, 8])
        self.assertNotIn(WEBHOOK, status["discord"])
        self.assertNotIn("x" * 20, status["discord"])

    def test_client_error_is_reported_at_once(self):
        host = FakeHost()
        host.answer(discord.execute_url(WEBHOOK), HttpResponse(400, b'{"message": "Invalid Form Body"}', 5))
        status = deliver(host, sample_alert(), self.cfg, ["discord"], ENV)
        self.assertIn("HTTP 400", status["discord"])
        self.assertEqual(host.slept, [])

    def test_missing_webhook(self):
        status = deliver(FakeHost(), sample_alert(), self.cfg, ["discord"], {})
        self.assertEqual(status["discord"], "error: discord: DISCORD_WEBHOOK_URL is not set")

    def test_ntfy_uses_bearer_token_and_base_url(self):
        host = FakeHost()
        host.answer("https://ntfy.example.com", ok(b'{"id": "abc"}'))
        status = deliver(host, sample_alert(), self.cfg, ["ntfy"], ENV)
        self.assertEqual(status["ntfy"], "sent (message abc)")
        _, method, url, headers, data = host.calls[-1]
        self.assertEqual((method, url), ("POST", "https://ntfy.example.com"))
        self.assertEqual(headers["Authorization"], "Bearer " + ENV["NTFY_TOKEN"])
        self.assertEqual(json.loads(data)["topic"], "watchdog")

    def test_nothing_to_send_after_the_filter(self):
        alert = Alert("h", [Notice(NEW, Result("load", "load.5min", WARNING, "load high"), NOW)], created=NOW)
        self.assertEqual(for_channel(alert, "critical").notices, [])
        status = deliver(FakeHost(), alert, self.cfg, ["ntfy"], ENV)
        self.assertEqual(status["ntfy"], "nothing to send")


if __name__ == "__main__":
    unittest.main()
