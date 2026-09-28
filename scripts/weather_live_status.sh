#!/usr/bin/env bash
set -u

LIVE="${LIVE:-/data/weather/live}"
DB="${WEATHER_RESEARCH_DB:-$LIVE/weather_research.sqlite3}"

echo "=== TIME ==="
date -Is

echo; echo "=== TMUX ==="
tmux ls 2>/dev/null | grep -E '^weather-' || true

echo; echo "=== CORE PROCESSES ==="
ps -eo pid,etime,cmd | grep -E '[r]un_weather_company_(paper_live|dashboard|forward_tournament|diagnostic_track).py|[r]un_remaining_heating_shadow.py|[r]un_live_alpha_shadow.py|[r]ecord_polymarket_us_weather.py|[s]core_weather_company_paper_settlements.py|[r]ecord_kalshi_weather_l2(_archive)?.py|[t]elegram_weather_paper_watcher.py|[b]uild_unified_signal_scorecard.py' || true

echo; echo "=== LIVE FILE FRESHNESS ==="
for f in weather_company_dashboard.json weather_company_paper_state.json weather_company_tournament_state.json weather_company_diagnostic_state.json weather_signal_scorecard.json; do
  if [[ -e "$LIVE/$f" ]]; then
    stat -c '%y  %10s  %n' "$LIVE/$f"
  else
    echo "MISSING $LIVE/$f"
  fi
done

echo; echo "=== CANONICAL RESEARCH STORE ==="
if [[ -f "$DB" ]]; then
  python3 - "$DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
for t in ('model_evaluations','signals','fills','settlements','paper_pnl','strategy_errors'):
    try:
        n = c.execute(f"select count(*) from {t}").fetchone()[0]
        print(f"{t}: {n}")
    except Exception as e:
        print(f"{t}: ERROR {e}")
PY
else
  echo "MISSING $DB"
fi

echo; echo "=== ECONOMIC SCORECARD ==="
sed -n '1,24p' "$LIVE/weather_signal_scorecard.md" 2>/dev/null || true

echo; echo "=== CREDENTIAL-GATED SERVICES ==="
[[ -f /root/.config/weather-alpha/live.env ]] && echo "live.env: present" || echo "live.env: MISSING"
[[ -f /root/.kalshi/weather_ws_private_key.pem ]] && echo "kalshi private key: present" || echo "kalshi private key: MISSING"
systemctl is-active --quiet weather-collector-kalshi-l2.service 2>/dev/null && echo "weather-collector-kalshi-l2: running" || echo "weather-collector-kalshi-l2: NOT RUNNING"
[[ -f /data/weather/status/kalshi_l2_archive_health.json ]] && stat -c "collector health: %y %s bytes" /data/weather/status/kalshi_l2_archive_health.json || echo "collector health: MISSING"
tmux has-session -t weather-poly-us 2>/dev/null && echo "weather-poly-us: running" || echo "weather-poly-us: not running"
[[ -f /data/weather/status/polymarket_us_weather_health.json ]] && stat -c "poly-us health: %y %s bytes" /data/weather/status/polymarket_us_weather_health.json || echo "poly-us health: MISSING"
tmux has-session -t weather-v2-shadow 2>/dev/null && echo "weather-v2-shadow: running" || echo "weather-v2-shadow: not running"
tmux has-session -t weather-alpha-shadow 2>/dev/null && echo "weather-alpha-shadow: running" || echo "weather-alpha-shadow: not running"
tmux has-session -t weather-telegram 2>/dev/null && echo "weather-telegram: running" || echo "weather-telegram: not running"

echo; echo "=== RECENT PROVIDER ERRORS ==="
tail -n 250 "$LIVE/weather_company_paper.log" 2>/dev/null \
  | grep -E 'obs_error|stale|429|Traceback|ERROR' \
  | tail -n 12 || true
