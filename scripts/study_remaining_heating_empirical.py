#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

import pandas as pd

from weather_alpha.research.remaining_heating import EmpiricalRemainingHeatingModel, contract_contains_value, empirical_bucket_probabilities

STATION_CITY = {'KNYC': 'new_york_city', 'KMDW': 'chicago', 'KMIA': 'miami', 'KLAX': 'los_angeles', 'KDEN': 'denver'}


def main() -> int:
    p = argparse.ArgumentParser(description='Walk-forward study of empirical remaining-heating / terminal-high model')
    p.add_argument('--history', type=Path, default=Path('/data/weather/normalized/features/remaining_heating_history.parquet'))
    p.add_argument('--buckets', type=Path, default=Path('/data/weather/normalized/markets/kalshi_weather_bucket_history_120d.json'))
    p.add_argument('--start-date', default='2026-07-01')
    p.add_argument('--end-date', default='2026-09-08')
    p.add_argument('--k', type=int, default=80)
    p.add_argument('--min-samples', type=int, default=30)
    p.add_argument('--hours', default='12,13,14,15,16,17,18')
    p.add_argument('--output', type=Path, default=Path('/data/weather/results/remaining_heating/empirical_walkforward.csv'))
    args = p.parse_args()

    frame = pd.read_parquet(args.history)
    frame['settlement_date'] = frame['settlement_date'].astype(str).str[:10]
    history_records = frame.to_dict('records')
    model = EmpiricalRemainingHeatingModel(history_records, k=args.k, min_samples=args.min_samples)
    bucket_rows = json.loads(args.buckets.read_text(encoding='utf-8'))
    bucket_map = {(str(r['city']), str(r['date'])): r for r in bucket_rows if r.get('validation_ok')}
    hours = {int(x) for x in args.hours.split(',') if x.strip()}
    test = frame[(frame.settlement_date >= args.start_date) & (frame.settlement_date <= args.end_date) &
                 frame.snapshot_hour_lst.isin(hours)].copy()

    out = []
    for row in test.to_dict('records'):
        pred = model.predict(row)
        if pred.median_residual_f is None:
            continue
        median_final = float(row['high_so_far_f']) + pred.median_residual_f
        city = STATION_CITY.get(str(row['station']))
        br = bucket_map.get((city, str(row['settlement_date']))) if city else None
        winner = None
        empirical_top = None
        empirical_top_prob = None
        current_high_bucket = None
        if br:
            winner = br['winner_contracts'][0]
            probs = empirical_bucket_probabilities(br['contracts'], pred.final_high_samples(float(row['high_so_far_f'])))
            if probs:
                empirical_top = max(probs, key=probs.get)
                empirical_top_prob = probs[empirical_top]
            for contract in br['contracts']:
                if contract_contains_value(contract, float(row['high_so_far_f'])):
                    current_high_bucket = contract.get('ticker')
                    break
        out.append({
            'station': row['station'], 'city': city, 'settlement_date': row['settlement_date'],
            'snapshot_hour_lst': int(row['snapshot_hour_lst']), 'high_so_far_f': float(row['high_so_far_f']),
            'official_cli_high_f': float(row['official_cli_high_f']),
            'sample_count': pred.sample_count, 'median_residual_f': pred.median_residual_f,
            'terminal_probability': pred.terminal_probability, 'median_final_high_f': median_final,
            'absolute_final_high_error_f': abs(float(row['official_cli_high_f']) - median_final),
            'winner_contract': winner, 'empirical_top_contract': empirical_top,
            'empirical_top_probability': empirical_top_prob, 'current_high_bucket': current_high_bucket,
            'empirical_top_hit': None if winner is None else empirical_top == winner,
            'current_high_bucket_hit': None if winner is None else current_high_bucket == winner,
        })
    result = pd.DataFrame(out)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    print(f'rows={len(result)} dates={result.settlement_date.nunique() if len(result) else 0} stations={result.station.nunique() if len(result) else 0}')
    if len(result):
        print(f"median_final_high_mae_f={result.absolute_final_high_error_f.mean():.4f}")
        with_bucket = result[result.winner_contract.notna()]
        print(f'bucket_rows={len(with_bucket)} bucket_dates={with_bucket.settlement_date.nunique()}')
        if len(with_bucket):
            print(f'empirical_top_bucket_hit_rate={with_bucket.empirical_top_hit.mean():.4f}')
            print(f'current_high_bucket_hit_rate={with_bucket.current_high_bucket_hit.mean():.4f}')
            by_hour = with_bucket.groupby('snapshot_hour_lst').agg(
                rows=('winner_contract','size'), dates=('settlement_date','nunique'),
                empirical_hit=('empirical_top_hit','mean'), current_high_hit=('current_high_bucket_hit','mean'),
                mae_f=('absolute_final_high_error_f','mean'), terminal_probability=('terminal_probability','mean'))
            print(by_hour.to_string())
    print(f'output={args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
