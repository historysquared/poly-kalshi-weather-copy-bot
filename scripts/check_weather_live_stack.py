from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DASH = Path('/data/weather/live/weather_company_dashboard.json')


def proc(pattern: str) -> bool:
    r = subprocess.run(['pgrep', '-f', pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def main() -> int:
    checks = {
        'paper_process': proc('run_weather_company_paper_live.py'),
        'scorer_process': proc('score_weather_company_paper_settlements.py'),
        'dashboard_process': proc('run_weather_company_dashboard.py'),
    }
    snap = {}
    if DASH.exists():
        try:
            snap = json.loads(DASH.read_text(encoding='utf-8'))
        except Exception:
            pass
    generated = snap.get('generated_at')
    age = None
    if generated:
        try:
            dt = datetime.fromisoformat(str(generated).replace('Z', '+00:00'))
            age = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
        except Exception:
            pass
    rows = snap.get('rows') if isinstance(snap.get('rows'), list) else []
    live_obs_rows = [r for r in rows if isinstance(r, dict) and r.get('latest_obs_time')]
    checks['dashboard_file'] = bool(snap)
    checks['dashboard_fresh_lt_3m'] = age is not None and age < 3.0
    checks['live_observations_present'] = bool(live_obs_rows)
    counts = snap.get('counts') if isinstance(snap.get('counts'), dict) else {}
    checks['contracts_present'] = int(counts.get('contracts') or 0) > 0
    status = 'HEALTHY' if all(checks.values()) else 'DEGRADED'
    print(f'status={status} dashboard_age_minutes={age if age is not None else "-"} contracts={counts.get("contracts","-")} events={counts.get("events","-")}')
    for k, v in checks.items():
        print(f'{k}={"PASS" if v else "FAIL"}')
    if live_obs_rows:
        for r in live_obs_rows:
            print(f"obs station={r.get('station')} latest={r.get('latest_obs_time')} source={r.get('obs_source')} temp={r.get('latest_temp_f')} high={r.get('high_so_far_f')} decision={r.get('decision')}")
    return 0 if status == 'HEALTHY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
