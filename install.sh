#!/bin/sh
# Install lumen-watchdog from this checkout.
#
#   install.sh [--prefix /opt/lumen-watchdog] [--config FILE] [--enable]
#
# Copies the program to the prefix, the configuration to /etc/lumen-watchdog/config.toml (an
# existing file is kept unless --config is given), the systemd units to /etc/systemd/system and,
# with --enable, starts the timer. Secrets go in /etc/lumen-watchdog/secrets.env (mode 600), which
# this script never writes.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
prefix=/opt/lumen-watchdog
config=""
enable=0
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) prefix=$2; shift 2 ;;
        --config) config=$2; shift 2 ;;
        --enable) enable=1; shift ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
    echo "python 3.11 or later is required (tomllib)" >&2
    exit 1
}

install -d -m 755 "$prefix" "$prefix/bin"
rm -rf "$prefix/lumen_watchdog"
cp -R "$here/lumen_watchdog" "$prefix/lumen_watchdog"
find "$prefix/lumen_watchdog" -name '__pycache__' -type d -prune -exec rm -rf {} +
install -m 755 "$here/bin/lumen-watchdog" "$prefix/bin/lumen-watchdog"
for dir in docs examples deploy systemd; do
    rm -rf "${prefix:?}/$dir"
    cp -R "$here/$dir" "$prefix/$dir"
done
cp "$here/LICENSE" "$here/NOTICE" "$here/README.md" "$prefix/"
ln -sf "$prefix/bin/lumen-watchdog" /usr/local/sbin/lumen-watchdog

install -d -m 700 /etc/lumen-watchdog
if [ -n "$config" ]; then
    install -m 600 "$config" /etc/lumen-watchdog/config.toml
elif [ ! -e /etc/lumen-watchdog/config.toml ]; then
    install -m 600 "$here/examples/config.toml" /etc/lumen-watchdog/config.toml
    echo "wrote /etc/lumen-watchdog/config.toml from the example: edit it before enabling the timer"
fi
"$prefix/bin/lumen-watchdog" --config /etc/lumen-watchdog/config.toml check-config

for unit in lumen-watchdog.service lumen-watchdog.timer lumen-watchdog-failure.service; do
    sed "s#/opt/lumen-watchdog#$prefix#g" "$here/systemd/$unit" > "/etc/systemd/system/$unit"
    chmod 644 "/etc/systemd/system/$unit"
done
systemctl daemon-reload
if [ "$enable" = 1 ]; then
    systemctl enable --now lumen-watchdog.timer
fi
echo "installed lumen-watchdog to $prefix"
