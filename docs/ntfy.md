# Self-hosted ntfy

[deploy/ntfy/](../deploy/ntfy/) runs [ntfy](https://ntfy.sh) in Docker for the push alerts. Only
two accounts can use it: the watchdog (write-only on one topic, with a token) and a phone
(read-only on the same topic, with a password). Everybody else gets `deny-all`.

| File | Purpose |
|---|---|
| `compose.yaml` | ntfy v2.38.2 pinned by digest, bound to `127.0.0.1:2586`, no capabilities, 96 MB memory limit |
| `server.yml` | `auth-file`, `auth-default-access: deny-all`, `require-login`, no sign-up, 24 h message cache, no attachments |
| `apache-ntfy.conf` | reverse proxy lines for Apache 2.4.47+ (`mod_proxy`, `mod_proxy_http`, WebSocket upgrade) |
| `setup-users.sh` | creates both accounts, the access rules and the watchdog token |

## Steps

1. **DNS**: an `A` (and `AAAA`) record for the ntfy host name pointing at the server. Without a
   proxy in between (a CDN proxy cuts the long-lived subscription connections).
2. **Web server**: create the host name with a TLS certificate in the hosting panel (or a plain
   `<VirtualHost *:443>`), then add the lines from `apache-ntfy.conf` to its HTTPS virtual host.
3. **Container**:

   ```bash
   sudo install -d -m 700 /opt/ntfy /opt/ntfy/data
   sudo cp deploy/ntfy/compose.yaml deploy/ntfy/server.yml deploy/ntfy/setup-users.sh /opt/ntfy/
   sudo sed -i 's#https://ntfy.example.com#https://ntfy.your-domain.example#' /opt/ntfy/server.yml
   cd /opt/ntfy && sudo docker compose up -d
   sudo sh setup-users.sh watchdog-server watchdog phone
   ```

   The script prints `NTFY_TOKEN=...` for `/etc/lumen-watchdog/secrets.env` and the phone
   account's password once.
4. **Watchdog**: in `config.toml` set `[ntfy] enabled = true`, `url` and `topic`, then
   `sudo lumen-watchdog test --channel ntfy`. On the same host, `url = "http://127.0.0.1:2586"`
   publishes without DNS or the web server; add an `[[checks.http.urls]]` entry for
   `https://<ntfy host>/v1/health` with `contains = "healthy"` so a broken public endpoint is
   reported.
5. **Phone**: in the ntfy app, add the server under *Settings → Manage users* with the phone
   account, then subscribe to the topic on that server.

## Priorities and tags

| Message | Priority | Tags |
|---|---|---|
| critical problem or event | 5 (max) | `rotating_light` |
| warning | 4 (high) | `warning` |
| only recoveries | 3 (default) | `white_check_mark` |
| test message | 3 (default) | `test_tube` |

The click URL (`[ntfy] click`) opens when the notification is tapped; up to three `[[ntfy.actions]]`
become view buttons.

## iOS

iOS apps get instant notifications from self-hosted servers only through `upstream-base-url`: the
server sends ntfy.sh a poll request that carries a hash of the topic and the message ID, never the
message text, and the phone then fetches the message from your server. Remove the line from
`server.yml` when no iOS device subscribes.
