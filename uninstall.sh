#!/bin/sh
# Remove lumen-watchdog: timer, units and program. Keeps /etc/lumen-watchdog and the state in
# /var/lib/lumen-watchdog unless --purge is given.
set -eu
prefix=/opt/lumen-watchdog
purge=0
for arg in "$@"; do
    case "$arg" in
        --purge) purge=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done
if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 1
fi
systemctl disable --now lumen-watchdog.timer 2>/dev/null || true
rm -f /etc/systemd/system/lumen-watchdog.service /etc/systemd/system/lumen-watchdog.timer \
      /etc/systemd/system/lumen-watchdog-failure.service /usr/local/sbin/lumen-watchdog
systemctl daemon-reload
rm -rf "$prefix"
if [ "$purge" = 1 ]; then
    rm -rf /etc/lumen-watchdog /var/lib/lumen-watchdog
fi
echo "removed lumen-watchdog"
