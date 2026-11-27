# Configuration

One TOML file, by default `/etc/lumen-watchdog/config.toml` (`--config` or `LUMEN_WATCHDOG_CONFIG`
for another path). Every setting has a default; an unknown setting is an error, so typos do not
go unnoticed. [examples/config.toml](../examples/config.toml) is a complete example.

## `[watchdog]`

| Setting | Default | Meaning |
|---|---|---|
| `host_name` | the host's FQDN | name shown in every message |
| `state_file` | `/var/lib/lumen-watchdog/state.json` | open problems, check caches, journal position |
| `env_file` | `/etc/lumen-watchdog/secrets.env` | `KEY=value` secrets, root-owned, mode 600 |
| `interval_minutes` | `5` | how often the timer runs (shown in the Discord footer) |
| `timezone` | host time zone | IANA name for the time in ntfy messages (Discord shows each reader's own time) |

## `[defaults]` and per-check overrides

These keys can also be set in any `[checks.<name>]` table for that check alone.

| Setting | Default | Meaning |
|---|---|---|
| `every_minutes` | `0` | run the check at most this often (0: every run); results are reused in between |
| `confirm_runs` | `1` | runs in a row a problem must fail before the first alert |
| `repeat_minutes` | `360` | reminder interval while a problem stays open (0: never) |
| `cooldown_minutes` | `15` | at least this long between two alerts for the same problem (flapping) |
| `recoveries` | `true` | send a message when an alerted problem is gone |

A problem that gets worse (warning to critical) is sent at once. Events (OOM kills, restarts)
are sent each time they happen and have no recovery.

## Checks

Every check has `enabled = true` by default.

### `systemd`

| Setting | Default | Meaning |
|---|---|---|
| `failed_units` | `true` | report every unit in the `failed` state (critical) |
| `ignore_failed` | `[]` | glob patterns of failed units to ignore |
| `watch` | `[]` | units that must be active; glob patterns match the units systemd has loaded |
| `optional` | `[]` | watched units (or patterns) that may be missing |
| `inactive_severity` | `critical` | severity of a watched unit that is not active |
| `restart_warn`, `restart_crit` | `1`, `5` | restarts by systemd (`NRestarts`) since the last run |

Watch timers (`lumen-spamfilter-train.timer`) rather than the one-shot services they start: a
one-shot service is inactive between runs. A glob such as `php8.*-fpm.service` matches loaded
units only, so an installed but unused PHP version is not reported; list a unit by its full name
when it must run.

### `oom`

`lookback_minutes` (60) is the window of the first run; later runs read the kernel log since the
previous run. `severity` (critical).

### `mail`

| Setting | Default | Meaning |
|---|---|---|
| `postqueue` | `/usr/sbin/postqueue` | empty string: skip the queue check |
| `queue_warn`, `queue_crit` | `50`, `200` | messages in all queues |
| `age_warn_minutes`, `age_crit_minutes` | `60`, `360` | age of the oldest queued message |
| `rspamd_url`, `rspamd_expect` | `http://127.0.0.1:11334/ping`, `pong` | empty URL: skip |
| `timeout_seconds` | `5` | per endpoint |
| `[[checks.mail.endpoints]]` | | `name`, `host` + `port` or `unix` (socket path), `tls`, `expect` (greeting prefix), `quit` (line sent before closing) |

### `disk`

`mounts` (empty: every mounted file system of a type in `fstypes`), `ignore` (globs),
`used_warn_percent` / `used_crit_percent` (85/95), `inodes_warn_percent` / `inodes_crit_percent`
(85/95).

### `memory`

`available_warn_percent` / `available_crit_percent` (10/5, of `MemTotal`), `swap_warn_percent` /
`swap_crit_percent` (80/95), `pressure_warn` / `pressure_crit` (10/30: percent of the last minute
in which all tasks stalled on memory, `/proc/pressure/memory` "full avg60").

### `load`

`per_cpu_warn` / `per_cpu_crit` (2.0/4.0) for the 5-minute load divided by the CPU count.

### `tls`

`hosts` (list of `name` or `name:port`; ports with direct TLS), `[[checks.tls.targets]]` with
`host`, `port`, `connect` (address to connect to, the host name is sent as SNI) and own
`warn_days` / `crit_days`. Defaults: `warn_days = 21`, `crit_days = 7`, `every_minutes = 60`. An
untrusted chain or a name mismatch is critical.

### `http`

`[[checks.http.urls]]` with `url`, `name`, `method` (GET), `expect_status` (`[200]`, after
redirects unless `follow_redirects = false`), `contains` (text the body must hold), `timeout_seconds`,
`latency_warn_ms` / `latency_crit_ms` (defaults from the table: 2500/10000).

### `backups`

`[[checks.backups.dirs]]` with `path`, `name`, `pattern` (`*`), `recursive` (true),
`max_age_hours` (26, warning) and `crit_age_hours` (twice `max_age_hours`). The walk stops after
`max_entries` (20000) entries. `every_minutes` defaults to 30.

### `apt`

Simulates `apt-get dist-upgrade` with the package lists apt already has. `security_warn` (1),
`security_crit` (0: never critical), `reboot_required` (`off`, `warning`, `critical`).
`every_minutes` defaults to 360 and `repeat_minutes` to 1440.

### `quota`

`mounts` (`["/"]`), `types` (`["user", "group"]`), `severity` (warning). Reads `quotaon -pa`.

## `[discord]`

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | `true` | |
| `webhook_env`, `ping_env` | `DISCORD_WEBHOOK_URL`, `DISCORD_PING_USER_ID` | environment variable names |
| `username` | `Lumen Watchdog` | webhook name override |
| `avatar_url`, `thumbnail_url` | the Luminare icon in this repository | avatar and header thumbnail |
| `min_severity` | `warning` | lowest severity sent (recoveries follow the severity they had) |
| `[[discord.buttons]]` | | up to 5 link buttons: `label`, `url` |

## `[ntfy]`

`enabled` (false), `url` (server base URL), `topic`, `token_env` (`NTFY_TOKEN`), `click`,
`min_severity` (warning), up to 3 `[[ntfy.actions]]` with `label` and `url`.
