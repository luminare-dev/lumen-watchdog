"""A fake Host for the check tests: canned command output, files, sockets and HTTP answers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lumen_watchdog.host import Completed, HttpResponse, StatVfs, TlsInfo  # noqa: E402

NOW = 1_795_800_000.0   # 2026-11-27, fixed for snapshots


class FakeHost:
    def __init__(self, now: float = NOW):
        self._now = now
        self.commands: dict[tuple, Completed] = {}
        self.prefix_commands: list[tuple[tuple, Completed]] = []
        self.files: dict[str, str] = {}
        self.vfs: dict[str, StatVfs | OSError] = {}
        self.banners: dict[str, tuple[str, float] | OSError] = {}
        self.certs: dict[tuple[str, int], TlsInfo | OSError] = {}
        self.http_answers: dict[str, list[HttpResponse]] = {}
        self.mtimes: dict[str, float | None | OSError] = {}
        self.calls: list = []
        self.slept: list[float] = []
        self.cpus = 2

    # --- setup helpers ---
    def on(self, argv: list[str], stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.commands[tuple(argv)] = Completed(returncode, stdout, stderr)

    def on_prefix(self, argv: list[str], stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.prefix_commands.append((tuple(argv), Completed(returncode, stdout, stderr)))

    def answer(self, url: str, *responses: HttpResponse) -> None:
        self.http_answers[url] = list(responses)

    # --- Host interface ---
    def now(self) -> float:
        return self._now

    def hostname(self) -> str:
        return "host.example.com"

    def run(self, argv, timeout=30.0):
        self.calls.append(("run", tuple(argv)))
        if tuple(argv) in self.commands:
            return self.commands[tuple(argv)]
        for prefix, result in self.prefix_commands:
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        return Completed(127, "", f"{argv[0]}: not faked")

    def read_text(self, path):
        return self.files.get(path)

    def exists(self, path):
        return path in self.files

    def statvfs(self, path):
        value = self.vfs[path]
        if isinstance(value, OSError):
            raise value
        return value

    def cpu_count(self):
        return self.cpus

    def newest_mtime(self, path, pattern, recursive, max_entries):
        value = self.mtimes.get(path, FileNotFoundError(2, "No such file or directory"))
        if isinstance(value, OSError):
            raise value
        return value, 1

    def banner(self, host, port, *, use_tls=False, timeout=5.0, unix="", quit_line=b""):
        key = unix or f"{host}:{port}"
        self.calls.append(("banner", key, use_tls, quit_line))
        value = self.banners.get(key, ConnectionRefusedError(111, "Connection refused"))
        if isinstance(value, OSError):
            raise value
        return value

    def tls_cert(self, host, port, *, sni="", timeout=8.0):
        self.calls.append(("tls", host, port, sni))
        value = self.certs.get((sni or host, port), ConnectionRefusedError(111, "Connection refused"))
        if isinstance(value, OSError):
            raise value
        return value

    def http(self, url, *, method="GET", timeout=10.0, follow_redirects=True, headers=None, data=None,
             max_body=65536):
        self.calls.append(("http", method, url, headers, data))
        queue = self.http_answers.get(url)
        if not queue:
            return HttpResponse(0, b"", 1.0, error="not faked")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def sleep(self, seconds):
        self.slept.append(seconds)


def ok(body: bytes = b"", ms: float = 50.0, status: int = 200, headers: dict | None = None) -> HttpResponse:
    return HttpResponse(status, body, ms, headers=headers or {})
