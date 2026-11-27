# 0005. Standard library only, one process per run

- **Status:** accepted
- **Date:** 2026-11-27

## Context

The watchdog runs on small hosts (4 GB of memory, where a virus scanner alone takes 1 GB) that
also serve web and mail. Installing Python packages on such a host adds upkeep and a second
package manager next to apt. A resident daemon holds memory all the time.

## Considered options

1. A daemon with `requests`, a TOML library and a scheduler.
2. A Python program with the standard library only (`tomllib`, `urllib`, `ssl`, `socket`,
   `subprocess`), started by a systemd timer.
3. Shell scripts.

## Decision

Chosen option: 2. Python 3.11 ships everything needed; the timer starts one short process every
five minutes, and systemd caps it (`MemoryMax=192M`, `Nice=10`, idle I/O). Shell scripts would
make the state handling, the JSON payloads and the tests much harder.

## Consequences

- Good: no packages to install or update on the host; one file tree under `/opt`.
- Good: nothing stays in memory between runs; the Python process peaks at about 30 MB (the `apt-get -s` child of the apt check at about 120 MB, every 6 hours).
- Bad: certificate parsing uses `ssl._ssl._test_decode_cert`, a private helper of the standard
  library. The TLS tests cover it, and a failure shows up as a check error, not a crash.
- Lint (ruff) runs in CI only, through `pipx run` with a pinned version.
