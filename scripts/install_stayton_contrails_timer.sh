#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/opt/poly-kalshi-weather-copy-bot}"
UNIT_DIR=/etc/systemd/system

install -m 0644 "$ROOT/deploy/systemd/weather-contrails-stayton.service" "$UNIT_DIR/weather-contrails-stayton.service"
install -m 0644 "$ROOT/deploy/systemd/weather-contrails-stayton.timer" "$UNIT_DIR/weather-contrails-stayton.timer"

systemctl daemon-reload
systemctl enable --now weather-contrails-stayton.timer
systemctl start weather-contrails-stayton.service

systemctl --no-pager --full status weather-contrails-stayton.service || true
systemctl --no-pager --full status weather-contrails-stayton.timer || true
