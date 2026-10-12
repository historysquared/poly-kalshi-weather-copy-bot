#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/opt/weather-alpha-strategy-release}"
PYTHON="${PYTHON:-/opt/weather-alpha-runtime/.venv/bin/python}"
LIVE="${LIVE:-/data/weather/live}"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    -h|--help) echo "Usage: start_paper_strategy_stack.sh [--dry-run]"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [[ ! -x "$PYTHON" ]]; then
  echo "missing python runtime: $PYTHON" >&2
  exit 1
fi
if [[ ! -f "$ROOT/scripts/run_weather_company_paper_live.py" ]]; then
  echo "invalid strategy release: $ROOT" >&2
  exit 1
fi

restart_session() {
  local name="$1"
  local command="$2"
  if [[ "$DRY_RUN" == true ]]; then
    printf 'DRY-RUN tmux session %s: %s
' "$name" "$command"
    return
  fi
  tmux kill-session -t "$name" 2>/dev/null || true
  tmux new-session -d -s "$name" "$command"
}

base="cd '$ROOT' && PYTHONPATH='$ROOT' '$PYTHON'"
restart_session weather-dash "$base scripts/run_weather_company_dashboard.py >> '$LIVE/weather_company_dashboard.log' 2>&1"
restart_session weather-paper "$base scripts/run_weather_company_paper_live.py >> '$LIVE/weather_company_paper.log' 2>&1"
restart_session weather-tourney "$base scripts/run_weather_company_forward_tournament.py >> '$LIVE/weather_company_tournament.log' 2>&1"
restart_session weather-diagnostic "$base scripts/run_weather_company_diagnostic_track.py >> '$LIVE/weather_company_diagnostic.log' 2>&1"
restart_session weather-v2-shadow "$base scripts/run_remaining_heating_shadow.py >> '$LIVE/remaining_heating_shadow.log' 2>&1"
restart_session weather-alpha-shadow "$base scripts/run_live_alpha_shadow.py >> '$LIVE/live_alpha_shadow.log' 2>&1"
restart_session weather-contrail-q90 "$base scripts/run_contrail_q90_monitor.py >> '$LIVE/contrail_q90_monitor.log' 2>&1"

telegram="cd '$ROOT' && set -a && source /root/.config/weather-alpha/live.env && source /root/.weather-contrails.env && set +a && PYTHONPATH='$ROOT' '$PYTHON' scripts/telegram_weather_paper_watcher.py >> '$LIVE/weather_telegram.log' 2>&1"
restart_session weather-telegram "$telegram"
scorecard="cd '$ROOT' && while true; do PYTHONPATH='$ROOT' '$PYTHON' scripts/build_unified_signal_scorecard.py >> '$LIVE/weather_signal_scorecard.log' 2>&1; sleep 60; done"
restart_session weather-scorecard "$scorecard"
restart_session weather-score "$base scripts/score_weather_company_paper_settlements.py >> '$LIVE/weather_company_paper_settlement.log' 2>&1"
restart_session weather-tourney-score "$base scripts/score_weather_company_paper_settlements.py --fills '$LIVE/weather_company_tournament_fills.jsonl' --output '$LIVE/weather_company_tournament_settled.jsonl' >> '$LIVE/weather_company_tournament_settlement.log' 2>&1"
restart_session weather-diagnostic-score "$base scripts/score_weather_company_paper_settlements.py --fills '$LIVE/weather_company_diagnostic_fills.jsonl' --output '$LIVE/weather_company_diagnostic_settled.jsonl' >> '$LIVE/weather_company_diagnostic_settlement.log' 2>&1"

if [[ "$DRY_RUN" == true ]]; then
  echo "paper strategy stack dry-run from $ROOT"
else
  echo "paper strategy stack restarted from $ROOT"
fi
echo "collectors untouched: kalshi L2, Polymarket US, catalog timers"
