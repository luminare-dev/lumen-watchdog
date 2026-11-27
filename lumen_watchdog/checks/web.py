"""TLS certificate expiry on live endpoints and HTTP status + latency checks."""

from __future__ import annotations

from ..model import CRITICAL, OK, WARNING, Result, grade


def tls_targets(cfg: dict) -> list[dict]:
    targets = []
    for entry in cfg.get("hosts", []):
        host, _, port = str(entry).partition(":")
        targets.append({"host": host, "port": int(port or 443)})
    for t in cfg.get("targets", []):
        targets.append({"host": t["host"], "port": int(t.get("port", 443)), "connect": t.get("connect", ""),
                        "warn_days": t.get("warn_days"), "crit_days": t.get("crit_days")})
    return targets


def run_tls(cfg: dict, host, data: dict) -> list[Result]:
    results = []
    timeout = float(cfg.get("timeout_seconds", 8))
    for t in tls_targets(cfg):
        name = t["host"] if t["port"] == 443 else f"{t['host']}:{t['port']}"
        key = f"tls:{name}"
        try:
            info = host.tls_cert(t.get("connect") or t["host"], t["port"], sni=t["host"], timeout=timeout)
        except OSError as exc:
            results.append(Result("tls", key, CRITICAL, f"{name}: TLS connection failed",
                                  detail=getattr(exc, "strerror", None) or str(exc) or type(exc).__name__))
            continue
        days = (info.not_after - host.now()) / 86400
        warn = t.get("warn_days") if t.get("warn_days") is not None else cfg.get("warn_days")
        crit = t.get("crit_days") if t.get("crit_days") is not None else cfg.get("crit_days")
        sev = grade(days, warn, crit, below=True)
        if days <= 0:
            results.append(Result("tls", key, CRITICAL, f"{name}: certificate expired {-days:.0f} days ago",
                                  detail=info.issuer))
        elif info.verify_error:
            results.append(Result("tls", key, CRITICAL, f"{name}: certificate not trusted ({info.verify_error})",
                                  detail=f"expires in {days:.0f} days; issuer {info.issuer}"))
        elif sev:
            results.append(Result("tls", key, sev, f"{name}: certificate expires in {days:.0f} days",
                                  detail=f"issuer {info.issuer}"))
    return results


def run_http(cfg: dict, host, data: dict) -> list[Result]:
    results = []
    for u in cfg.get("urls", []):
        url = u["url"]
        name = u.get("name") or url
        key = f"http:{name}"
        timeout = float(u.get("timeout_seconds", cfg.get("timeout_seconds", 15)))
        resp = host.http(url, method=u.get("method", "GET"), timeout=timeout,
                         follow_redirects=bool(u.get("follow_redirects", True)))
        if resp.error:
            results.append(Result("http", key, CRITICAL, f"{name}: no response", detail=resp.error[:200]))
            continue
        expect = u.get("expect_status", [200])
        if isinstance(expect, int):
            expect = [expect]
        if resp.status not in expect:
            results.append(Result("http", key, CRITICAL, f"{name}: HTTP {resp.status}",
                                  detail=f"expected {', '.join(map(str, expect))}"))
            continue
        contains = u.get("contains", "")
        if contains and contains.encode() not in resp.body:
            results.append(Result("http", key, CRITICAL, f"{name}: expected text missing",
                                  detail=f"the page does not contain {contains!r}"))
            continue
        warn = u.get("latency_warn_ms", cfg.get("latency_warn_ms"))
        crit = u.get("latency_crit_ms", cfg.get("latency_crit_ms"))
        sev = grade(resp.elapsed_ms, warn, crit)
        if sev > OK:
            results.append(Result("http", f"{key}#latency", WARNING if sev == WARNING else CRITICAL,
                                  f"{name}: slow response ({resp.elapsed_ms:.0f} ms)"))
    return results
