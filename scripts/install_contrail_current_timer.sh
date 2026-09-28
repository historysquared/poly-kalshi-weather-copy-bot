#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-/opt/weather-alpha-strategy-release}"
UNIT_DIR=/etc/systemd/system

for unit in \
  weather-contrails-current.service weather-contrails-current.timer \
  weather-contrails-history.service weather-contrails-history.timer; do
  install -m 0644 "$ROOT/deploy/systemd/$unit" "$UNIT_DIR/$unit"
done

systemctl daemon-reload
systemctl enable --now weather-contrails-current.timer weather-contrails-history.timer
systemctl start weather-contrails-current.service

# Retire the obsolete Stayton-only timer if it is present; it referenced the old deploy path.
systemctl disable --now weather-contrails-stayton.timer 2>/dev/null || true
systemctl stop weather-contrails-stayton.service 2>/dev/null || true

systemctl --no-pager --full status weather-contrails-current.timer || true
systemctl --no-pager --full status weather-contrails-history.timer || true
