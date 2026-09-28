#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON="${WEATHER_PYTHON:-$ROOT/.venv/bin/python}"
ENV_FILE="${WEATHER_LIVE_ENV:-/root/.config/weather-alpha/live.env}"
DATA_DIR="${WEATHER_LIVE_DATA_DIR:-/data/weather/live}"

if [[ ! -x "$PYTHON" ]]; then
  echo "missing executable Python: $PYTHON" >&2
  exit 2
fi

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
  if tmux list-sessions -F "#S" 2>/dev/null | grep -Fxq -- "$name"; then
    echo "already running: $name"
    return 0
  fi
  tmux new-session -d -s "$name" "$*"
  echo "started: $name"
}

start_tmux weather-dash \
  "cd '$ROOT' && PYTHONPATH=. '$PYTHON' scripts/run_weather_company_dashboard.py >> '$DATA_DIR/weather_company_dashboard.log' 2>&1"
start_tmux weather-paper \
  "cd '$ROOT' && PYTHONPATH=. '$PYTHON' scripts/run_weather_company_paper_live.py >> '$DATA_DIR/weather_company_paper.log' 2>&1"
start_tmux weather-score \
  "cd '$ROOT' && PYTHONPATH=. '$PYTHON' scripts/score_weather_company_paper_settlements.py >> '$DATA_DIR/weather_company_paper_settlement.log' 2>&1"
start_tmux weather-tourney \
  "cd '$ROOT' && PYTHONPATH=. '$PYTHON' scripts/run_weather_company_forward_tournament.py >> '$DATA_DIR/weather_company_tournament.log' 2>&1"
start_tmux weather-diagnostic \
  "cd '$ROOT' && PYTHONPATH=. '$PYTHON' scripts/run_weather_company_diagnostic_track.py >> '$DATA_DIR/weather_company_diagnostic.log' 2>&1"
start_tmux weather-tourney-score \
  "cd '$ROOT' && PYTHONPATH=. '$PYTHON' scripts/score_weather_company_paper_settlements.py --fills '$DATA_DIR/weather_company_tournament_fills.jsonl' --output '$DATA_DIR/weather_company_tournament_settled.jsonl' >> '$DATA_DIR/weather_company_tournament_settlement.log' 2>&1"
start_tmux weather-diagnostic-score \
  "cd '$ROOT' && PYTHONPATH=. '$PYTHON' scripts/score_weather_company_paper_settlements.py --fills '$DATA_DIR/weather_company_diagnostic_fills.jsonl' --output '$DATA_DIR/weather_company_diagnostic_settled.jsonl' >> '$DATA_DIR/weather_company_diagnostic_settlement.log' 2>&1"
start_tmux weather-scorecard \
  "cd '$ROOT' && while true; do PYTHONPATH=. '$PYTHON' scripts/build_unified_signal_scorecard.py >> '$DATA_DIR/weather_signal_scorecard.log' 2>&1; sleep 60; done"

if systemctl is-active --quiet weather-collector-kalshi-l2.service 2>/dev/null; then
  echo "already running: weather-collector-kalshi-l2.service (durable archive collector)"
elif [[ "${WEATHER_ENABLE_LEGACY_L2:-0}" == "1" && -n "${KALSHI_API_KEY_ID:-}" && -n "${KALSHI_PRIVATE_KEY_PATH:-}" ]]; then
  start_tmux weather-l2 \
    "cd '$ROOT' && set -a && [[ -f '$ENV_FILE' ]] && source '$ENV_FILE' || true; set +a; PYTHONPATH=. '$PYTHON' scripts/record_kalshi_weather_l2.py >> '$DATA_DIR/kalshi_l2.log' 2>&1"
else
  echo "not started: legacy weather-l2 (durable collector preferred; set WEATHER_ENABLE_LEGACY_L2=1 only for explicit fallback)"
fi

start_tmux weather-poly-us \
  "cd '$ROOT' && PYTHONPATH=. '$PYTHON' scripts/record_polymarket_us_weather.py >> '$DATA_DIR/polymarket_us_weather.log' 2>&1"

if [[ -f /data/weather/normalized/features/remaining_heating_history.parquet ]]; then
  start_tmux weather-v2-shadow \
    "cd '$ROOT' && PYTHONPATH=. '$PYTHON' scripts/run_remaining_heating_shadow.py >> '$DATA_DIR/remaining_heating_shadow.log' 2>&1"
else
  echo "not started: weather-v2-shadow (remaining-heating history missing)"
fi

start_tmux weather-alpha-shadow \
  "cd '$ROOT' && PYTHONPATH=. '$PYTHON' scripts/run_live_alpha_shadow.py >> '$DATA_DIR/live_alpha_shadow.log' 2>&1"

if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
  start_tmux weather-telegram \
    "cd '$ROOT' && set -a && [[ -f '$ENV_FILE' ]] && source '$ENV_FILE' || true; set +a; PYTHONPATH=. '$PYTHON' scripts/telegram_weather_paper_watcher.py >> '$DATA_DIR/weather_telegram.log' 2>&1"
else
  echo "not started: weather-telegram (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in $ENV_FILE)"
fi

echo
echo "=== WEATHER TMUX ==="
tmux ls | grep -E '^weather-' || true
