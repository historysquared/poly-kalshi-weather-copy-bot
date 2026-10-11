#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/opt/weather-alpha-strategy-release}"
SOURCE_ROOT="${SOURCE_ROOT:-$ROOT}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
PYTHON="${PYTHON:-/opt/weather-alpha-signal-pipeline/.venv/bin/python}"
DRY_RUN=false
RETIRE_STAYTON=false

usage() {
  cat <<'EOF'
Usage: install_contrail_current_timer.sh [--dry-run] [--retire-stayton]

Installs the all-city contrail snapshot/history units and q90 research monitor.
The existing Stayton-only timer is preserved unless --retire-stayton is explicit.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --retire-stayton) RETIRE_STAYTON=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

EXPECTED_ROOT=/opt/weather-alpha-strategy-release
if [[ "$ROOT" != "$EXPECTED_ROOT" && "$DRY_RUN" != true ]]; then
  echo "refusing install: ROOT must be $EXPECTED_ROOT because deployed units use that stable release path" >&2
  exit 2
fi
if [[ ! -d "$SOURCE_ROOT" ]]; then
  echo "missing source root: $SOURCE_ROOT" >&2
  exit 2
fi
if [[ "$DRY_RUN" != true && ! -d "$ROOT" ]]; then
  echo "missing release root: $ROOT; create/promote the release path before installing units" >&2
  exit 2
fi
if [[ "$DRY_RUN" != true && ! -x "$PYTHON" ]]; then
  echo "missing python runtime: $PYTHON" >&2
  exit 2
fi

units=(
  weather-contrails-current.service
  weather-contrails-current.timer
  weather-contrails-history.service
  weather-contrails-history.timer
  weather-contrails-q90.service
)
for unit in "${units[@]}"; do
  [[ -f "$SOURCE_ROOT/deploy/systemd/$unit" ]] || { echo "missing unit: $unit" >&2; exit 2; }
done
for script in record_contrail_current.py refresh_contrail_hourly_history.py run_contrail_q90_monitor.py; do
  [[ -f "$SOURCE_ROOT/scripts/$script" ]] || { echo "missing runtime script: $script" >&2; exit 2; }
done

run() {
  if [[ "$DRY_RUN" == true ]]; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '
'
  else
    "$@"
  fi
}

for unit in "${units[@]}"; do
  run install -m 0644 "$SOURCE_ROOT/deploy/systemd/$unit" "$UNIT_DIR/$unit"
done
run systemctl daemon-reload
run systemctl enable --now weather-contrails-current.timer weather-contrails-history.timer weather-contrails-q90.service
run systemctl start weather-contrails-current.service

if [[ "$RETIRE_STAYTON" == true ]]; then
  if [[ "$DRY_RUN" == true ]]; then
    run systemctl disable --now weather-contrails-stayton.timer
    run systemctl stop weather-contrails-stayton.service
  else
    systemctl disable --now weather-contrails-stayton.timer 2>/dev/null || true
    systemctl stop weather-contrails-stayton.service 2>/dev/null || true
  fi
else
  echo "keeping existing Stayton-only timer/service unchanged; use --retire-stayton only after migration is verified"
fi

if [[ "$DRY_RUN" != true ]]; then
  systemctl --no-pager --full status weather-contrails-current.timer || true
  systemctl --no-pager --full status weather-contrails-history.timer || true
  systemctl --no-pager --full status weather-contrails-q90.service || true
fi
