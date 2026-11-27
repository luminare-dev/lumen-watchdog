# 0006. No mount-namespace sandbox for the service

- **Status:** accepted
- **Date:** 2026-11-27

## Context

systemd hardening options such as `ProtectSystem=`, `PrivateTmp=`, `PrivateDevices=` and
`ProtectHome=` set up a mount namespace before the service starts. On the host this project was
written for, the kernel once failed to create these namespaces: every unit that used them failed
to start (the IMAP server among them) and stayed down for almost two days without anyone noticing.
A watchdog with the same options would have failed in the same way and reported nothing.

## Considered options

1. Full sandboxing, like other services.
2. No mount-namespace options; hardening that needs no namespace only.

## Decision

Chosen option: 2. The service keeps `NoNewPrivileges=yes`, `UMask=0077`, a memory cap and low CPU
and I/O priority, and uses `StateDirectory=` for its state. It reads system state and writes only
its own state file.

## Consequences

- Good: when namespaces break, the failed units are still reported.
- Bad: the service runs as root without a read-only view of the file system. The code runs a fixed
  list of read-only commands (`systemctl`, `journalctl`, `postqueue -j`, `apt-get -s`,
  `quotaon -p`) without a shell.
