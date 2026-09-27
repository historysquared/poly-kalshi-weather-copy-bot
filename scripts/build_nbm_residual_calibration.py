#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

from weather_alpha.research.forecast_residuals import build_prior_only_residual_calibration


def main() -> int:
    p = argparse.ArgumentParser(description='Build prior-only station NBM residual calibration history')
    p.add_argument('--nbm', type=Path, required=True)
    p.add_argument('--buckets', type=Path, required=True)
    p.add_argument('--lookback-days', type=int, default=90)
    p.add_argument('--min-history', type=int, default=30)
    p.add_argument('--output', type=Path, default=Path('/data/weather/normalized/forecasts/nbm_residual_calibration.json'))
    args = p.parse_args()

    nbm = json.loads(args.nbm.read_text(encoding='utf-8'))
    buckets = json.loads(args.buckets.read_text(encoding='utf-8'))
    actual = {(str(r.get('city')), str(r.get('date'))): r.get('expiration_value')
              for r in buckets if r.get('validation_ok') and r.get('expiration_value') is not None}
    joined = []
    for row in nbm:
        if row.get('error') or row.get('nbm_max_f') is None:
            continue
        key = (str(row.get('location_key')), str(row.get('date')))
        if key not in actual:
            continue
        joined.append({
            'location_key': key[0], 'station': row.get('station'), 'date': key[1],
            'nbm_max_f': row.get('nbm_max_f'), 'actual_high_f': actual[key],
        })
    calibrated = build_prior_only_residual_calibration(
        joined, lookback_days=args.lookback_days, min_history=args.min_history
    )
    payload = [r.to_dict() for r in calibrated]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    eligible = [r for r in calibrated if r.eligible]
    raw_mae = mean(abs(r.raw_error_f) for r in eligible) if eligible else None
    adj_mae = mean(abs(r.adjusted_error_f) for r in eligible if r.adjusted_error_f is not None) if eligible else None
    print(f'joined={len(joined)} eligible={len(eligible)} locations={len({r.location_key for r in eligible})}')
    print(f'raw_mae_f={raw_mae} adjusted_mae_f={adj_mae} output={args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
