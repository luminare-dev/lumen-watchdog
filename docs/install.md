# Installation

lumen-watchdog runs as root from a systemd timer. It needs Python 3.11 or later and nothing else
from outside the standard library.

## 1. Program and configuration

```bash
git clone https://github.com/luminare-dev/lumen-watchdog
cd lumen-watchdog
sudo ./install.sh                     # or: sudo ./install.sh --config my-config.toml
```

`install.sh` copies the program to `/opt/lumen-watchdog`, links `/usr/local/sbin/lumen-watchdog`,
writes `/etc/lumen-watchdog/config.toml` from [examples/config.toml](../examples/config.toml) when
there is none yet, validates it and installs three units:

| Unit | Purpose |
|---|---|
| `lumen-watchdog.timer` | starts the service 2 minutes after boot and then 5 minutes after each run |
| `lumen-watchdog.service` | one run of the due checks (`MemoryMax=192M`, `Nice=10`, idle I/O) |
| `lumen-watchdog-failure.service` | `OnFailure=` of the service: tells the channels that a run failed |

Edit the configuration ([configuration.md](configuration.md)) and check it:

```bash
sudo lumen-watchdog check-config
sudo lumen-watchdog run --dry-run      # runs every check now, prints the payloads, sends nothing
```

## 2. Secrets

Secrets live in `/etc/lumen-watchdog/secrets.env`, owned by root with mode 600. The service reads
it with `EnvironmentFile=`, and manual commands read it through `watchdog.env_file`. A file that
other users can read is ignored with a warning.

```bash
sudo install -m 600 /dev/null /etc/lumen-watchdog/secrets.env
sudoedit /etc/lumen-watchdog/secrets.env
```

```sh
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>
DISCORD_PING_USER_ID=<your Discord user ID>
NTFY_TOKEN=tk_<token from deploy/ntfy/setup-users.sh>
```

The webhook URL is never printed: not by `--dry-run`, not in errors, not in the journal. Create the
webhook in Discord under *Server settings → Integrations → Webhooks*. The user ID comes from
*Settings → Advanced → Developer Mode*, then *Copy User ID* on the profile.

## 3. Test and enable

```bash
sudo lumen-watchdog test --channel discord
sudo lumen-watchdog test --channel ntfy
sudo systemctl enable --now lumen-watchdog.timer
systemctl list-timers lumen-watchdog.timer
journalctl -u lumen-watchdog -n 20
```

## Updating and removing

Run `sudo ./install.sh` again from a newer checkout; the configuration and the state are kept.
`sudo ./uninstall.sh` removes the program and the units, `--purge` also removes
`/etc/lumen-watchdog` and `/var/lib/lumen-watchdog`.

## ntfy

The self-hosted ntfy server is optional and described in [ntfy.md](ntfy.md).
