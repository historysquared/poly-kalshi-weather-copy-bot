#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/opt/poly-kalshi-weather-copy-bot}"
ENV_FILE="${WEATHER_LIVE_ENV:-/root/.config/weather-alpha/live.env}"
DATA_DIR="/data/weather/live"

cd "$ROOT"
source .venv/bin/activate

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

mkdir -p "$DATA_DIR"

start_tmux() {
  local name="$1"
  shift
  if tmux has-session -t "$name" 2>/dev/null; then
    echo "already running: $name"
    return 0
  fi
  tmux new-session -d -s "$name" "$*"
  echo "started: $name"
}

start_tmux weather-dash   "cd '$ROOT' && source .venv/bin/activate && PYTHONPATH=. python scripts/run_weather_company_dashboard.py >> '$DATA_DIR/weather_company_dashboard.log' 2>&1"

start_tmux weather-paper   "cd '$ROOT' && source .venv/bin/activate && PYTHONPATH=. python scripts/run_weather_company_paper_live.py >> '$DATA_DIR/weather_company_paper.log' 2>&1"

start_tmux weather-tourney   "cd '$ROOT' && source .venv/bin/activate && PYTHONPATH=. python scripts/run_weather_company_forward_tournament.py >> '$DATA_DIR/weather_company_tournament.log' 2>&1"

start_tmux weather-diagnostic   "cd '$ROOT' && source .venv/bin/activate && PYTHONPATH=. python scripts/run_weather_company_diagnostic_track.py >> '$DATA_DIR/weather_company_diagnostic.log' 2>&1"

if [[ -n "${KALSHI_API_KEY_ID:-}" && -n "${KALSHI_PRIVATE_KEY_PATH:-}" ]]; then
  start_tmux weather-l2     "cd '$ROOT' && source .venv/bin/activate && set -a && [[ -f '$ENV_FILE' ]] && source '$ENV_FILE' || true; set +a; PYTHONPATH=. python scripts/record_kalshi_weather_l2.py >> '$DATA_DIR/kalshi_l2.log' 2>&1"
else
  echo "not started: weather-l2 (set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in $ENV_FILE)"
fi

if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
  start_tmux weather-telegram     "cd '$ROOT' && source .venv/bin/activate && set -a && [[ -f '$ENV_FILE' ]] && source '$ENV_FILE' || true; set +a; PYTHONPATH=. python scripts/telegram_weather_paper_watcher.py >> '$DATA_DIR/weather_telegram.log' 2>&1"
else
  echo "not started: weather-telegram (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in $ENV_FILE)"
fi

echo
echo "=== WEATHER TMUX ==="
tmux ls | grep -E '^weather-' || true
