"""Every side effect the checks need, in one class, so the tests can replace it with a fake.

The checks never call subprocess, socket, ssl or open() themselves. They ask a ``Host``.
"""

from __future__ import annotations

import contextlib
import os
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import PROJECT_URL, __version__

USER_AGENT = f"lumen-watchdog/{__version__} (+{PROJECT_URL})"


@dataclass
class Completed:
    returncode: int
    stdout: str
    stderr: str = ""


@dataclass
class HttpResponse:
    status: int
    body: bytes
    elapsed_ms: float
    error: str = ""          # set when no HTTP response arrived (DNS, refused, timeout, TLS)
    headers: dict = field(default_factory=dict)


@dataclass
class TlsInfo:
    not_after: float         # epoch seconds
    subject: str
    issuer: str
    verify_error: str = ""   # empty when the chain and the hostname verified


@dataclass
class StatVfs:
    total: int
    free: int
    inodes_total: int
    inodes_free: int


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib hook
        return None


class Host:
    """The real host. Network calls use short timeouts; nothing is written outside the state file."""

    def now(self) -> float:
        return time.time()

    def hostname(self) -> str:
        return socket.getfqdn()

    # -- processes and files -------------------------------------------------------------------

    def run(self, argv: list[str], timeout: float = 30.0) -> Completed:
        env = {"LC_ALL": "C", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
               "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0"}
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env,
                                  stdin=subprocess.DEVNULL, check=False, errors="replace")
        except FileNotFoundError:
            return Completed(127, "", f"{argv[0]}: not found")
        except subprocess.TimeoutExpired:
            return Completed(124, "", f"{argv[0]}: timed out after {timeout:.0f} s")
        return Completed(proc.returncode, proc.stdout, proc.stderr)

    def read_text(self, path: str) -> str | None:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except (FileNotFoundError, NotADirectoryError):
            return None

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def statvfs(self, path: str) -> StatVfs:
        st = os.statvfs(path)
        return StatVfs(st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize, st.f_files, st.f_favail)

    def cpu_count(self) -> int:
        return os.cpu_count() or 1

    def newest_mtime(self, path: str, pattern: str, recursive: bool, max_entries: int) -> tuple[float | None, int]:
        """Newest mtime of the files under ``path`` matching ``pattern``; stops after ``max_entries``."""
        import fnmatch

        newest: float | None = None
        seen = 0
        stack = [path]
        while stack and seen < max_entries:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        seen += 1
                        if seen > max_entries:
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if recursive:
                                    stack.append(entry.path)
                                continue
                            if not fnmatch.fnmatch(entry.name, pattern):
                                continue
                            mtime = entry.stat(follow_symlinks=False).st_mtime
                        except OSError:
                            continue
                        if newest is None or mtime > newest:
                            newest = mtime
            except (PermissionError, FileNotFoundError, NotADirectoryError):
                if current == path:
                    raise
        return newest, seen

    # -- network -------------------------------------------------------------------------------

    def banner(self, host: str, port: int, *, use_tls: bool = False, timeout: float = 5.0,
               unix: str = "", quit_line: bytes = b"") -> tuple[str, float]:
        """Connect, read the first line the server sends, optionally say goodbye. Raises OSError."""
        start = time.monotonic()
        if unix:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(unix)
        else:
            sock = socket.create_connection((host, port), timeout=timeout)
        try:
            if use_tls:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE   # reachability only; certificates have their own check
                sock = ctx.wrap_socket(sock, server_hostname=host)
            data = b""
            while b"\n" not in data and len(data) < 1024:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                data += chunk
            if quit_line:
                with contextlib.suppress(OSError):
                    sock.sendall(quit_line)
            return data.decode("utf-8", "replace").strip(), (time.monotonic() - start) * 1000
        finally:
            sock.close()

    def tls_cert(self, host: str, port: int, *, sni: str = "", timeout: float = 8.0) -> TlsInfo:
        """Fetch the served certificate. Verification errors are reported, not raised."""
        name = sni or host
        verify_error = ""
        der = None
        ctx = ssl.create_default_context()
        try:
            with socket.create_connection((host, port), timeout=timeout) as raw, \
                    ctx.wrap_socket(raw, server_hostname=name) as tls:
                der = tls.getpeercert(binary_form=True)
        except ssl.SSLCertVerificationError as exc:
            verify_error = exc.verify_message or str(exc)
        if der is None:
            loose = ssl.create_default_context()
            loose.check_hostname = False
            loose.verify_mode = ssl.CERT_NONE
            with socket.create_connection((host, port), timeout=timeout) as raw, \
                    loose.wrap_socket(raw, server_hostname=name) as tls:
                der = tls.getpeercert(binary_form=True)
        return _parse_der(der, verify_error)

    def http(self, url: str, *, method: str = "GET", timeout: float = 10.0,
             follow_redirects: bool = True, headers: dict | None = None, data: bytes | None = None,
             max_body: int = 65536) -> HttpResponse:
        req = urllib.request.Request(url, method=method, data=data,
                                     headers={"User-Agent": USER_AGENT, **(headers or {})})
        handlers = [] if follow_redirects else [_NoRedirect()]
        opener = urllib.request.build_opener(*handlers)
        start = time.monotonic()
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read(max_body)
                return HttpResponse(resp.status, body, (time.monotonic() - start) * 1000,
                                    headers={k.lower(): v for k, v in resp.headers.items()})
        except urllib.error.HTTPError as exc:
            body = exc.read(max_body) if exc.fp else b""
            return HttpResponse(exc.code, body, (time.monotonic() - start) * 1000,
                                headers={k.lower(): v for k, v in (exc.headers or {}).items()})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            return HttpResponse(0, b"", (time.monotonic() - start) * 1000, error=str(reason) or type(exc).__name__)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _parse_der(der: bytes, verify_error: str) -> TlsInfo:
    """Read notAfter, subject and issuer from a DER certificate with the standard library only."""
    # ssl has no public DER parser; the private helper _test_decode_cert needs a file. Use it via a
    # temporary PEM file, which is the documented round trip for ssl.DER_cert_to_PEM_cert.
    import tempfile

    pem = ssl.DER_cert_to_PEM_cert(der)
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=True) as fh:
        fh.write(pem)
        fh.flush()
        info = ssl._ssl._test_decode_cert(fh.name)  # noqa: SLF001 - stdlib-only certificate parsing
    not_after = ssl.cert_time_to_seconds(info["notAfter"])

    def flat(rdns) -> str:
        return ", ".join(f"{k}={v}" for rdn in rdns for (k, v) in rdn)

    return TlsInfo(not_after, flat(info.get("subject", ())), flat(info.get("issuer", ())), verify_error)
