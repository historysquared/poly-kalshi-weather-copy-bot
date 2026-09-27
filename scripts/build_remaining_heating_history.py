#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from weather_alpha.backtest.causal_settlement import TimedTemperature, lock_gate, surface_state
from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import registered_station_clocks


def parse_hours(value: str) -> list[int]:
    hours = [int(x) for x in value.split(',') if x.strip()]
    if not hours or any(x < 0 or x > 23 for x in hours):
        raise argparse.ArgumentTypeError('snapshot hours must be comma-separated integers 0..23')
    return sorted(set(hours))


def load_station_rows(raw_dir: Path, station: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob(f'{station}_*.jsonl')):
        with path.open(encoding='utf-8') as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get('temperature_f') in (None, '') or not row.get('valid_time'):
                    continue
                row['_dt'] = pd.Timestamp(row['valid_time']).to_pydatetime()
                rows.append(row)
    rows.sort(key=lambda r: r['_dt'])
    return rows


def latest_aux(rows: list[dict[str, Any]], end_index: int) -> dict[str, float | None]:
    if end_index <= 0:
        return {'dewpoint_f': None, 'wind_speed_kt': None, 'wind_direction_deg': None}
    row = rows[end_index - 1]
    def f(key: str) -> float | None:
        value = row.get(key)
        return None if value in (None, '') else float(value)
    return {'dewpoint_f': f('dewpoint_f'), 'wind_speed_kt': f('wind_speed_kt'),
            'wind_direction_deg': f('wind_direction_deg')}


def main() -> int:
    p = argparse.ArgumentParser(description='Build causal late-day remaining-heating training history')
    p.add_argument('--raw-dir', type=Path, default=Path('/data/weather/raw/asos/history_months'))
    p.add_argument('--cli', type=Path, default=Path('/data/weather/normalized/settlements/nws_cli_daily.parquet'))
    p.add_argument('--snapshot-hours-lst', type=parse_hours, default=parse_hours('10,11,12,13,14,15,16,17,18'))
    p.add_argument('--start-date', type=date.fromisoformat, default=None)
    p.add_argument('--end-date', type=date.fromisoformat, default=None)
    p.add_argument('--output', type=Path, default=Path('/data/weather/normalized/features/remaining_heating_history.parquet'))
    args = p.parse_args()

    clocks = registered_station_clocks()
    cli = pd.read_parquet(args.cli)
    cli['valid_date'] = cli['valid_date'].astype(str).str[:10]
    if args.start_date:
        cli = cli[cli['valid_date'] >= args.start_date.isoformat()]
    if args.end_date:
        cli = cli[cli['valid_date'] <= args.end_date.isoformat()]
    cli = cli[cli['station'].isin(clocks)]

    out: list[dict[str, Any]] = []
    for station, station_cli in cli.groupby('station', sort=True):
        rows = load_station_rows(args.raw_dir, station)
        times = [r['_dt'] for r in rows]
        print(f'station={station} raw_obs={len(rows)} cli_days={len(station_cli)}', flush=True)
        for cli_row in station_cli.itertuples(index=False):
            day = date.fromisoformat(str(cli_row.valid_date))
            official = None if pd.isna(cli_row.high_f) else float(cli_row.high_f)
            if official is None:
                continue
            window = local_standard_settlement_window(clocks[station], day)
            start_i = bisect.bisect_left(times, window.start_utc)
            end_i = bisect.bisect_left(times, window.end_utc)
            day_rows = rows[start_i:end_i]
            if not day_rows:
                continue
            day_times = [r['_dt'] for r in day_rows]
            temps = [TimedTemperature(r['_dt'], Decimal(str(r['temperature_f']))) for r in day_rows]
            for hour in args.snapshot_hours_lst:
                as_of = window.start_utc + timedelta(hours=hour)
                if as_of >= window.end_utc:
                    continue
                idx = bisect.bisect_right(day_times, as_of)
                state = surface_state(temps[:idx], as_of=as_of)
                if state.high_so_far_f is None or state.latest_temp_f is None:
                    continue
                aux = latest_aux(day_rows, idx)
                gate, gate_reasons = lock_gate(state)
                high = float(state.high_so_far_f)
                latest = float(state.latest_temp_f)
                remaining = official - high
                dew = aux['dewpoint_f']
                out.append({
                    'station': station, 'settlement_date': day.isoformat(),
                    'snapshot_hour_lst': hour, 'snapshot_time_utc': as_of.isoformat(),
                    'official_cli_high_f': official, 'high_so_far_f': high,
                    'latest_temp_f': latest,
                    'minutes_since_high': None if state.minutes_since_high is None else float(state.minutes_since_high),
                    'drop_from_high_f': None if state.drop_from_high_f is None else float(state.drop_from_high_f),
                    'slope_15m_f_per_min': None if state.slope_15m_f_per_min is None else float(state.slope_15m_f_per_min),
                    'latest_dewpoint_f': dew,
                    'dewpoint_depression_f': None if dew is None else latest - dew,
                    'wind_speed_kt': aux['wind_speed_kt'], 'wind_direction_deg': aux['wind_direction_deg'],
                    'observations_used': state.observations_used,
                    'control_lock_pass': gate, 'control_lock_reasons': list(gate_reasons),
                    'remaining_heating_f': remaining,
                    'new_official_high_after_snapshot': official > high,
                    'official_equals_high_so_far': abs(remaining) < 1e-9,
                    'source_label': 'CAUSAL_ASOS_AT_OR_BEFORE_SNAPSHOT_PLUS_NEXT_CLI_TARGET',
                })
    frame = pd.DataFrame(out)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.output, index=False)
    print(f'output={args.output} rows={len(frame)} stations={frame.station.nunique() if len(frame) else 0} '
          f'dates={frame.settlement_date.nunique() if len(frame) else 0}')
    if len(frame):
        print(frame.groupby('snapshot_hour_lst')['new_official_high_after_snapshot'].agg(['count','mean']).to_string())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
