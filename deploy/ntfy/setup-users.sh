#!/bin/sh
# Create the two ntfy accounts for lumen-watchdog in a running ntfy container:
#   - the publisher (default "watchdog"): write-only on the topic, with an access token;
#   - the reader (default "phone"): read-only on the topic, with a password for the phone app.
# Prints NTFY_TOKEN=... (for the watchdog's secrets file) and the reader's password once.
#
# Usage: setup-users.sh <topic> [publisher] [reader]   (run in the folder with compose.yaml)
set -eu
topic=${1:?usage: setup-users.sh <topic> [publisher] [reader]}
publisher=${2:-watchdog}
reader=${3:-phone}

ntfy() { docker compose exec -T "$@"; }

random_password() {
    # 24 characters from the kernel's random source; no tool beyond coreutils needed.
    tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24
}

pub_pass=$(random_password)
read_pass=$(random_password)

if ! ntfy ntfy ntfy user list 2>/dev/null | grep -q "^user $publisher "; then
    ntfy -e NTFY_PASSWORD="$pub_pass" ntfy ntfy user add --role=user "$publisher" >/dev/null
fi
if ! ntfy ntfy ntfy user list 2>/dev/null | grep -q "^user $reader "; then
    ntfy -e NTFY_PASSWORD="$read_pass" ntfy ntfy user add --role=user "$reader" >/dev/null
    reader_new=1
else
    reader_new=0
fi
ntfy ntfy ntfy access "$publisher" "$topic" write-only >/dev/null
ntfy ntfy ntfy access "$reader" "$topic" read-only >/dev/null

token=$(ntfy ntfy ntfy token add --label=lumen-watchdog "$publisher" 2>&1 | grep -o 'tk_[A-Za-z0-9]*' | head -n 1)
if [ -z "$token" ]; then
    echo "could not create a token for $publisher" >&2
    exit 1
fi
echo "NTFY_TOKEN=$token"
if [ "$reader_new" = 1 ]; then
    echo "NTFY_READER=$reader"
    echo "NTFY_READER_PASSWORD=$read_pass"
fi
