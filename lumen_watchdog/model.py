"""Results, severities and notifications shared by the checks, the engine and the channels."""

from __future__ import annotations

from dataclasses import dataclass, field

OK = 0
WARNING = 1
CRITICAL = 2

SEVERITY_NAMES = {OK: "ok", WARNING: "warning", CRITICAL: "critical"}
SEVERITY_BY_NAME = {v: k for k, v in SEVERITY_NAMES.items()}


def severity_name(sev: int) -> str:
    return SEVERITY_NAMES.get(sev, "unknown")


def parse_severity(value: str) -> int:
    try:
        return SEVERITY_BY_NAME[str(value).lower()]
    except KeyError:
        raise ValueError(f"unknown severity {value!r} (use ok, warning or critical)") from None


def grade(value: float, warn: float | None, crit: float | None, *, below: bool = False) -> int:
    """Severity of a measured value against two thresholds (``below`` for "lower is worse")."""
    def hit(limit: float | None) -> bool:
        if limit is None:
            return False
        return value <= limit if below else value >= limit

    if hit(crit):
        return CRITICAL
    if hit(warn):
        return WARNING
    return OK


@dataclass
class Result:
    """One finding of a check.

    ``key`` identifies the finding across runs (for example ``systemd.unit:dovecot.service``);
    the engine keeps its history under that key. An ``event`` result (an OOM kill) is reported
    each time it appears and never gets a recovery message.
    """

    check: str
    key: str
    severity: int
    summary: str
    detail: str = ""
    event: bool = False

    @property
    def ok(self) -> bool:
        return self.severity == OK


# Notification kinds, in the order a message lists them.
NEW = "new"
ESCALATED = "escalated"
REMINDER = "reminder"
EVENT = "event"
RECOVERED = "recovered"
KIND_ORDER = (NEW, ESCALATED, EVENT, REMINDER, RECOVERED)


@dataclass
class Notice:
    """One line of an alert message: a result and why it is being sent now."""

    kind: str
    result: Result
    since: float | None = None   # epoch seconds when the problem was first seen


@dataclass
class Alert:
    """Everything one run sends: the notices plus context for the message header."""

    host: str
    notices: list[Notice] = field(default_factory=list)
    open_problems: int = 0        # problems still open after this run (alerted or not)
    test: bool = False            # a "lumen-watchdog test" message
    failure: str = ""             # set when the watchdog itself failed (OnFailure unit)
    created: float = 0.0

    @property
    def severity(self) -> int:
        """Highest severity among the problem notices (OK when only recoveries)."""
        sev = [n.result.severity for n in self.notices if n.kind != RECOVERED]
        return max(sev) if sev else OK

    @property
    def only_recoveries(self) -> bool:
        return bool(self.notices) and all(n.kind == RECOVERED for n in self.notices)

    def by_kind(self, kind: str) -> list[Notice]:
        return [n for n in self.notices if n.kind == kind]
