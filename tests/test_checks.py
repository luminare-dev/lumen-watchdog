"""Every check against a fake host."""

from __future__ import annotations

import json
import unittest

from fakes import NOW, FakeHost, ok

from lumen_watchdog.checks import mail, oom, resources, system, systemd, web
from lumen_watchdog.host import HttpResponse, StatVfs, TlsInfo
from lumen_watchdog.model import CRITICAL, OK, WARNING

SC = "/usr/bin/systemctl"
FAILED = [SC, "list-units", "--failed", "--all", "--plain", "--no-legend", "--no-pager"]
SHOW_PROPS = "Id,LoadState,ActiveState,SubState,NRestarts,Result"


def show_block(unit, load="loaded", active="active", sub="running", restarts=0):
    return f"Id={unit}\nLoadState={load}\nActiveState={active}\nSubState={sub}\nNRestarts={restarts}\nResult=success\n"


class SystemdTest(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        self.host.on(FAILED, "")

    def test_failed_units_are_critical_and_ignore_list_applies(self):
        self.host.on(FAILED, "dovecot.service loaded failed failed Dovecot IMAP/POP3\n"
                             "● apport-autoreport.service loaded failed failed Process error reports\n")
        res = systemd.run({"ignore_failed": ["apport-*"], "watch": []}, self.host, {})
        self.assertEqual([(r.key, r.severity) for r in res], [("systemd.unit:dovecot.service", CRITICAL)])
        self.assertIn("has failed", res[0].summary)

    def test_watch_list_inactive_missing_and_globs(self):
        self.host.on([SC, "list-units", "--all", "--plain", "--no-legend", "--no-pager", "php8.*-fpm.service"],
                     "php8.3-fpm.service loaded active running PHP\nphp8.4-fpm.service loaded inactive dead PHP\n")
        self.host.on([SC, "list-units", "--all", "--plain", "--no-legend", "--no-pager", "nope-*.service"], "")
        units = ["dovecot.service", "php8.3-fpm.service", "php8.4-fpm.service", "gone.service", "opt.service"]
        self.host.on([SC, "show", "--no-pager", "-p", SHOW_PROPS, *units],
                     "\n".join([show_block("dovecot.service"), show_block("php8.3-fpm.service"),
                                show_block("php8.4-fpm.service", active="inactive", sub="dead"),
                                show_block("gone.service", load="not-found", active="inactive", sub="dead"),
                                show_block("opt.service", load="not-found", active="inactive", sub="dead")]))
        cfg = {"watch": ["dovecot.service", "php8.*-fpm.service", "gone.service", "opt.service", "nope-*.service"],
               "optional": ["opt.service"], "inactive_severity": "critical"}
        res = {r.key: r for r in systemd.run(cfg, self.host, {})}
        self.assertEqual(res["systemd.unit:php8.4-fpm.service"].severity, CRITICAL)
        self.assertEqual(res["systemd.unit:gone.service"].severity, WARNING)
        self.assertEqual(res["systemd.watch:nope-*.service"].severity, WARNING)
        self.assertNotIn("systemd.unit:opt.service", res)
        self.assertNotIn("systemd.unit:dovecot.service", res)

    def test_restart_counter_is_an_event_after_the_first_run(self):
        args = [SC, "show", "--no-pager", "-p", SHOW_PROPS, "clamav-daemon.service"]
        cfg = {"watch": ["clamav-daemon.service"], "restart_warn": 1, "restart_crit": 3}
        data: dict = {}
        self.host.on(args, show_block("clamav-daemon.service", restarts=2))
        self.assertEqual(systemd.run(cfg, self.host, data), [])          # first run: baseline only
        self.host.on(args, show_block("clamav-daemon.service", restarts=3))
        res = systemd.run(cfg, self.host, data)
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0].event)
        self.assertEqual(res[0].severity, WARNING)
        self.assertIn("restarted once", res[0].summary)
        self.host.on(args, show_block("clamav-daemon.service", restarts=7))
        self.assertEqual(systemd.run(cfg, self.host, data)[0].severity, CRITICAL)

    def test_systemctl_failure_raises(self):
        self.host.on(FAILED, "", returncode=1, stderr="Failed to connect to bus")
        with self.assertRaises(RuntimeError):
            systemd.run({"watch": []}, self.host, {})

    def test_parse_show_handles_several_blocks(self):
        parsed = systemd.parse_show(show_block("a.service") + "\n" + show_block("b.timer", sub="waiting"))
        self.assertEqual(parsed["b.timer"]["SubState"], "waiting")


class OomTest(unittest.TestCase):
    def test_counts_kills_since_the_last_run_and_moves_the_window(self):
        host = FakeHost()
        last = NOW - 300
        host.on_prefix(["/usr/bin/journalctl"],
                       f"{last - 10:.6f} web kernel: Out of memory: Killed process 1 (old) total-vm:1kB\n"
                       f"{NOW - 120:.6f} web kernel: Out of memory: Killed process 812 (clamd) total-vm:1kB\n"
                       f"{NOW - 60:.6f} web kernel: Memory cgroup out of memory: Killed process 900 (clamd)\n"
                       f"{NOW - 50:.6f} web kernel: Out of memory: Killed process 901 (php-fpm8.4)\n")
        data = {"last_ts": last}
        res = oom.run({"severity": "critical"}, host, data)
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0].event)
        self.assertEqual(res[0].severity, CRITICAL)
        self.assertEqual(res[0].summary, "OOM killer stopped 3 processes: clamd (2x), php-fpm8.4")
        self.assertEqual(data["last_ts"], NOW - 5)
        cmd = [c for c in host.calls if c[0] == "run"][0][1]
        self.assertIn(f"@{int(last)}", cmd)

    def test_quiet_kernel_log(self):
        host = FakeHost()
        host.on_prefix(["/usr/bin/journalctl"], "", returncode=1)
        data: dict = {}
        self.assertEqual(oom.run({"lookback_minutes": 60}, host, data), [])
        self.assertEqual(data["last_ts"], NOW - 5)
        cmd = [c for c in host.calls if c[0] == "run"][0][1]
        self.assertIn(f"@{int(NOW - 3600)}", cmd)


def queue_line(arrival, queue="deferred"):
    return json.dumps({"queue_name": queue, "queue_id": "ABC", "arrival_time": arrival, "message_size": 1})


class MailTest(unittest.TestCase):
    CFG = {"postqueue": "/usr/sbin/postqueue", "queue_warn": 2, "queue_crit": 4, "age_warn_minutes": 60,
           "age_crit_minutes": 360, "rspamd_url": "http://127.0.0.1:11334/ping", "rspamd_expect": "pong",
           "endpoints": [
               {"name": "IMAP", "host": "127.0.0.1", "port": 143, "expect": "* OK", "quit": "a LOGOUT"},
               {"name": "LMTP", "unix": "/run/lmtp", "expect": "220", "quit": "QUIT"},
           ]}

    def host(self):
        host = FakeHost()
        host.on(["/usr/sbin/postqueue", "-j"], "")
        host.banners["127.0.0.1:143"] = ("* OK [CAPABILITY IMAP4rev1] Dovecot ready.", 2.0)
        host.banners["/run/lmtp"] = ("220 web.example.com Dovecot ready.", 1.0)
        host.answer("http://127.0.0.1:11334/ping", ok(b"pong\r\n"))
        return host

    def test_all_good(self):
        host = self.host()
        self.assertEqual(mail.run(self.CFG, host, {}), [])
        self.assertIn(("banner", "127.0.0.1:143", False, b"a LOGOUT\r\n"), host.calls)

    def test_queue_size_and_age(self):
        host = self.host()
        host.on(["/usr/sbin/postqueue", "-j"],
                "\n".join([queue_line(NOW - 7 * 3600), queue_line(NOW - 60, "active"), queue_line(NOW - 10)]))
        res = {r.key: r for r in mail.run(self.CFG, host, {})}
        self.assertEqual(res["mail.queue"].severity, WARNING)
        self.assertEqual(res["mail.queue"].summary, "Postfix queue holds 3 messages (active 1, deferred 2)")
        self.assertEqual(res["mail.queue_age"].severity, CRITICAL)
        self.assertEqual(res["mail.queue_age"].summary, "oldest queued message is 7 h old")

    def test_unreadable_queue(self):
        host = self.host()
        host.on(["/usr/sbin/postqueue", "-j"], "", returncode=69, stderr="postqueue: fatal: Queue report unavailable")
        res = mail.run(self.CFG, host, {})
        self.assertEqual(res[0].key, "mail.queue")
        self.assertEqual(res[0].severity, CRITICAL)

    def test_endpoint_down_and_wrong_banner_and_rspamd(self):
        host = self.host()
        del host.banners["/run/lmtp"]
        host.banners["127.0.0.1:143"] = ("-ERR something else", 1.0)
        host.answer("http://127.0.0.1:11334/ping", HttpResponse(0, b"", 1.0, error="Connection refused"))
        res = {r.key: r for r in mail.run(self.CFG, host, {})}
        self.assertEqual(res["mail.endpoint:LMTP"].summary, "LMTP is not reachable (/run/lmtp)")
        self.assertIn("answered unexpectedly", res["mail.endpoint:IMAP"].summary)
        self.assertEqual(res["mail.rspamd"].summary, "Rspamd is not responding")

    def test_rspamd_wrong_answer(self):
        host = self.host()
        host.answer("http://127.0.0.1:11334/ping", ok(b"oops", status=500))
        res = mail.run(self.CFG, host, {})
        self.assertEqual(res[0].summary, "Rspamd answered HTTP 500")


GIB = 1024 ** 3


class ResourcesTest(unittest.TestCase):
    def test_disk_auto_mounts_space_and_inodes(self):
        host = FakeHost()
        host.files["/proc/mounts"] = ("/dev/sda1 / ext4 rw 0 0\nproc /proc proc rw 0 0\n"
                                      "/dev/sda15 /boot/efi vfat rw 0 0\n/dev/sdb1 /srv/back\\040up xfs rw 0 0\n")
        host.vfs["/"] = StatVfs(100 * GIB, 4 * GIB, 1000, 100)
        host.vfs["/boot/efi"] = StatVfs(GIB, GIB // 2, 0, 0)
        host.vfs["/srv/back up"] = OSError(5, "Input/output error")
        cfg = {"mounts": [], "fstypes": ["ext4", "vfat", "xfs"], "ignore": [], "used_warn_percent": 85,
               "used_crit_percent": 95, "inodes_warn_percent": 85, "inodes_crit_percent": 95}
        res = {r.key: r for r in resources.run_disk(cfg, host, {})}
        self.assertEqual(res["disk.space:/"].severity, CRITICAL)
        self.assertEqual(res["disk.space:/"].summary, "/ is 96% full (4.0 GiB free)")
        self.assertEqual(res["disk.inodes:/"].severity, WARNING)
        self.assertEqual(res["disk.space:/srv/back up"].severity, CRITICAL)
        self.assertNotIn("disk.space:/boot/efi", res)

    def test_memory_swap_pressure(self):
        host = FakeHost()
        host.files["/proc/meminfo"] = ("MemTotal: 4000000 kB\nMemFree: 100000 kB\nMemAvailable: 180000 kB\n"
                                       "SwapTotal: 2000000 kB\nSwapFree: 200000 kB\n")
        host.files["/proc/pressure/memory"] = ("some avg10=1.00 avg60=20.00 avg300=1.00 total=1\n"
                                               "full avg10=0.50 avg60=12.50 avg300=0.10 total=1\n")
        cfg = {"available_warn_percent": 10, "available_crit_percent": 5, "swap_warn_percent": 80,
               "swap_crit_percent": 95, "pressure_warn": 10, "pressure_crit": 30}
        res = {r.key: r for r in resources.run_memory(cfg, host, {})}
        self.assertEqual(res["memory.available"].severity, CRITICAL)
        self.assertEqual(res["memory.swap"].severity, WARNING)
        self.assertEqual(res["memory.pressure"].summary, "memory pressure: tasks stalled 12.5% of the last minute")

    def test_memory_fine_and_no_swap(self):
        host = FakeHost()
        host.files["/proc/meminfo"] = "MemTotal: 4000000 kB\nMemAvailable: 2000000 kB\nSwapTotal: 0 kB\n"
        self.assertEqual(resources.run_memory({"available_warn_percent": 10}, host, {}), [])

    def test_load_per_cpu(self):
        host = FakeHost()
        host.cpus = 3
        host.files["/proc/loadavg"] = "9.00 7.50 3.00 2/500 1234\n"
        res = resources.run_load({"per_cpu_warn": 2.0, "per_cpu_crit": 4.0}, host, {})
        self.assertEqual(res[0].severity, WARNING)
        self.assertEqual(res[0].summary, "load 7.50 over 5 min on 3 CPUs (2.5 per CPU)")
        host.files["/proc/loadavg"] = "0.10 0.20 0.30 1/400 1\n"
        self.assertEqual(resources.run_load({"per_cpu_warn": 2.0}, host, {}), [])


class TlsTest(unittest.TestCase):
    def test_expiry_trust_and_connection(self):
        host = FakeHost()
        day = 86400
        host.certs[("good.example.com", 443)] = TlsInfo(NOW + 60 * day, "CN=good", "CN=R11")
        host.certs[("soon.example.com", 443)] = TlsInfo(NOW + 10 * day, "CN=soon", "CN=R11")
        host.certs[("urgent.example.com", 993)] = TlsInfo(NOW + 3 * day, "CN=urgent", "CN=R11")
        host.certs[("old.example.com", 443)] = TlsInfo(NOW - 2 * day, "CN=old", "CN=R11", "certificate has expired")
        host.certs[("self.example.com", 443)] = TlsInfo(NOW + 300 * day, "CN=self", "CN=self",
                                                        "self-signed certificate")
        cfg = {"warn_days": 21, "crit_days": 7,
               "hosts": ["good.example.com", "soon.example.com", "urgent.example.com:993", "old.example.com",
                         "self.example.com", "down.example.com"],
               "targets": [{"host": "inner.example.com", "connect": "127.0.0.1", "warn_days": 90}]}
        host.certs[("inner.example.com", 443)] = TlsInfo(NOW + 60 * day, "CN=inner", "CN=R11")
        res = {r.key: r for r in web.run_tls(cfg, host, {})}
        self.assertNotIn("tls:good.example.com", res)
        self.assertEqual(res["tls:soon.example.com"].severity, WARNING)
        self.assertEqual(res["tls:soon.example.com"].summary, "soon.example.com: certificate expires in 10 days")
        self.assertEqual(res["tls:urgent.example.com:993"].severity, CRITICAL)
        self.assertIn("expired 2 days ago", res["tls:old.example.com"].summary)
        self.assertIn("not trusted (self-signed certificate)", res["tls:self.example.com"].summary)
        self.assertEqual(res["tls:down.example.com"].summary, "down.example.com: TLS connection failed")
        self.assertEqual(res["tls:inner.example.com"].severity, WARNING)
        self.assertIn(("tls", "127.0.0.1", 443, "inner.example.com"), host.calls)


class HttpTest(unittest.TestCase):
    def test_status_text_latency_and_errors(self):
        host = FakeHost()
        host.answer("https://a.example.com/", ok(b"<title>Login</title>", ms=120))
        host.answer("https://b.example.com/", ok(b"", status=502))
        host.answer("https://c.example.com/", ok(b"hello", ms=4000))
        host.answer("https://d.example.com/", HttpResponse(0, b"", 10, error="timed out"))
        host.answer("https://e.example.com/", ok(b"other"))
        host.answer("https://f.example.com/", ok(b"", status=302))
        cfg = {"latency_warn_ms": 2500, "latency_crit_ms": 10000, "urls": [
            {"name": "A", "url": "https://a.example.com/", "contains": "Login"},
            {"name": "B", "url": "https://b.example.com/"},
            {"name": "C", "url": "https://c.example.com/"},
            {"name": "D", "url": "https://d.example.com/"},
            {"name": "E", "url": "https://e.example.com/", "contains": "Login"},
            {"url": "https://f.example.com/", "expect_status": [301, 302], "follow_redirects": False},
        ]}
        res = {r.key: r for r in web.run_http(cfg, host, {})}
        self.assertEqual(sorted(res), ["http:B", "http:C#latency", "http:D", "http:E"])
        self.assertEqual(res["http:B"].summary, "B: HTTP 502")
        self.assertEqual(res["http:C#latency"].severity, WARNING)
        self.assertEqual(res["http:D"].detail, "timed out")
        self.assertEqual(res["http:E"].summary, "E: expected text missing")


class BackupsTest(unittest.TestCase):
    def test_ages(self):
        host = FakeHost()
        host.mtimes["/backup/fresh"] = NOW - 3600
        host.mtimes["/backup/stale"] = NOW - 30 * 3600
        host.mtimes["/backup/dead"] = NOW - 80 * 3600
        host.mtimes["/backup/empty"] = None
        cfg = {"dirs": [{"path": "/backup/fresh", "max_age_hours": 26},
                        {"path": "/backup/stale", "name": "nightly", "max_age_hours": 26},
                        {"path": "/backup/dead", "max_age_hours": 26},
                        {"path": "/backup/empty"},
                        {"path": "/backup/missing"}]}
        res = {r.key: r for r in system.run_backups(cfg, host, {})}
        self.assertNotIn("backups:/backup/fresh", res)
        self.assertEqual(res["backups:nightly"].severity, WARNING)
        self.assertEqual(res["backups:nightly"].summary, "newest backup in nightly is 30 h old")
        self.assertEqual(res["backups:/backup/dead"].severity, CRITICAL)
        self.assertIn("holds no backup files", res["backups:/backup/empty"].summary)
        self.assertIn("cannot be read", res["backups:/backup/missing"].summary)


APT_OUT = """NOTE: This is only a simulation!
Inst libssl3t64 [3.0.13-0ubuntu3.5] (3.0.13-0ubuntu3.6 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst openssl [3.0.13-0ubuntu3.5] (3.0.13-0ubuntu3.6 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst snapd [2.66] (2.67 Ubuntu:24.04/noble-updates [amd64])
Conf libssl3t64 (3.0.13-0ubuntu3.6 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
"""


class AptTest(unittest.TestCase):
    def test_security_updates_and_reboot(self):
        host = FakeHost()
        host.on_prefix([system.APT_GET], APT_OUT)
        host.files["/var/run/reboot-required"] = "*** System restart required ***\n"
        host.files["/var/run/reboot-required.pkgs"] = "linux-image-6.8.0-152-generic\nlinux-base\nlinux-base\n"
        res = {r.key: r for r in system.run_apt({"security_warn": 1, "security_crit": 0,
                                                 "reboot_required": "warning"}, host, {})}
        self.assertEqual(res["apt.security"].summary, "2 security updates pending (3 in all)")
        self.assertEqual(res["apt.security"].severity, WARNING)
        self.assertEqual(res["apt.security"].detail, "libssl3t64, openssl")
        self.assertEqual(res["apt.reboot"].detail, "linux-base, linux-image-6.8.0-152-generic")

    def test_clean_system_and_reboot_off(self):
        host = FakeHost()
        host.on_prefix([system.APT_GET], "Inst snapd [2.66] (2.67 Ubuntu:24.04/noble-updates [amd64])\n")
        host.files["/var/run/reboot-required"] = ""
        self.assertEqual(system.run_apt({"security_warn": 1, "reboot_required": "off"}, host, {}), [])

    def test_apt_error(self):
        host = FakeHost()
        host.on_prefix([system.APT_GET], "", returncode=100, stderr="E: broken")
        res = system.run_apt({"security_warn": 1, "reboot_required": "off"}, host, {})
        self.assertEqual(res[0].summary, "pending updates cannot be read")


class QuotaTest(unittest.TestCase):
    def test_on_off_missing(self):
        host = FakeHost()
        host.on([system.QUOTAON, "-pa"], "group quota on / (/dev/sda1) is on\nuser quota on / (/dev/sda1) is off\n"
                                         "project quota on / (/dev/sda1) is off\n", returncode=1)
        res = {r.key: r for r in system.run_quota({"mounts": ["/", "/home"], "types": ["user", "group"],
                                                   "severity": "critical"}, host, {})}
        self.assertEqual(sorted(res), ["quota:/:user", "quota:/home:group", "quota:/home:user"])
        self.assertEqual(res["quota:/:user"].summary, "user quota on / is off")
        self.assertEqual(res["quota:/:user"].severity, CRITICAL)

    def test_all_on(self):
        host = FakeHost()
        host.on([system.QUOTAON, "-pa"], "group quota on / (/dev/sda1) is on\nuser quota on / (/dev/sda1) is on\n")
        self.assertEqual(system.run_quota({"mounts": ["/"], "types": ["user", "group"]}, host, {}), [])


class GradeTest(unittest.TestCase):
    def test_grade(self):
        from lumen_watchdog.model import grade
        self.assertEqual(grade(5, 10, 20), OK)
        self.assertEqual(grade(10, 10, 20), WARNING)
        self.assertEqual(grade(25, 10, 20), CRITICAL)
        self.assertEqual(grade(3, 10, 5, below=True), CRITICAL)
        self.assertEqual(grade(8, 10, 5, below=True), WARNING)
        self.assertEqual(grade(8, None, None), OK)


if __name__ == "__main__":
    unittest.main()
