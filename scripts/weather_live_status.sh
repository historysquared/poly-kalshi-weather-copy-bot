#!/usr/bin/env bash
set -u

LIVE=/data/weather/live

echo "=== TMUX ==="
tmux ls 2>/dev/null | grep -E '^weather-' || true

echo
echo "=== PROCESSES ==="
ps aux | grep -E '[r]un_weather_company_(paper_live|dashboard|forward_tournament|diagnostic_track).py|[r]ecord_kalshi_weather_l2.py|[t]elegram_weather_paper_watcher.py' || true

echo
echo "=== DASHBOARD ==="
head -n 20 "$LIVE/weather_company_dashboard.txt" 2>/dev/null || true

echo
echo "=== L2 HEALTH ==="
cat "$LIVE/kalshi_l2_health.json" 2>/dev/null || true

echo
echo "=== L2 LATEST FILES ==="
find "$LIVE/kalshi_l2" -type f -name '*.jsonl.gz' -printf '%TY-%Tm-%Td %TH:%TM  %10s  %p\n' 2>/dev/null | sort | tail -n 5

echo
echo "=== LAST SIGNALS ==="
for f in   weather_company_paper_signals.jsonl   weather_company_tournament_signals.jsonl   weather_company_diagnostic_signals.jsonl; do
  echo "--- $f"
  tail -n 2 "$LIVE/$f" 2>/dev/null || true
done

echo
echo "=== LAST MARKOUTS ==="
tail -n 10 "$LIVE/weather_company_latency_markouts.jsonl" 2>/dev/null || true

echo
echo "=== TELEGRAM LOG ==="
tail -n 10 "$LIVE/weather_telegram.log" 2>/dev/null || true
