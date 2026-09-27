#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from weather_alpha.engine.models import MarketSnapshot
from weather_alpha.engine.recorder import StateStore
from weather_alpha.research.remaining_heating import EmpiricalRemainingHeatingModel, EmpiricalRemainingHeatingStrategy
from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import station_clock


def D(value: Any) -> float | None:
    if value in (None, ''):
        return None
    try:
        return float(value)
    except Exception:
        return None


def latest_snapshot(path: Path, max_bytes: int = 8_000_000) -> tuple[str | None, list[dict[str, Any]]]:
    if not path.exists() or path.stat().st_size == 0:
        return None, []
    size = path.stat().st_size
    with path.open('rb') as fh:
        start = max(0, size - max_bytes)
        fh.seek(start)
        raw = fh.read()
    if start:
        n = raw.find(b'\n')
        raw = raw[n + 1:] if n >= 0 else b''
    rows = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get('snapshot_time'):
            rows.append(row)
    if not rows:
        return None, []
    latest = max(str(r['snapshot_time']) for r in rows)
    return latest, [r for r in rows if str(r.get('snapshot_time')) == latest]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + '\n')


def load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(path)


def strike_metadata(row: dict[str, Any]) -> dict[str, Any]:
    shape = str(row.get('shape') or '').lower()
    return {
        'strike_type': {'bucket': 'between', 'below': 'less', 'above': 'greater'}.get(shape, shape),
        'floor_strike': D(row.get('lower')),
        'cap_strike': D(row.get('upper')),
        'station': row.get('station'),
        'settlement_date': row.get('settlement_date'),
    }


def current_features(row: dict[str, Any], snapshot: datetime, model: EmpiricalRemainingHeatingModel) -> tuple[dict[str, object], str]:
    station = str(row['station'])
    day = date.fromisoformat(str(row['settlement_date'])[:10])
    window = local_standard_settlement_window(station_clock(station), day)
    elapsed = (snapshot - window.start_utc).total_seconds() / 3600.0
    hour = max(0, min(23, int(elapsed)))
    current = {
        'station': station, 'settlement_date': day.isoformat(), 'snapshot_hour_lst': hour,
        'high_so_far_f': D(row.get('high_so_far_f')),
        'latest_temp_f': D(row.get('latest_temp_f')),
        'minutes_since_high': D(row.get('minutes_since_high')),
        'drop_from_high_f': D(row.get('drop_from_high_f')),
        'slope_15m_f_per_min': D(row.get('slope_15m_f_per_min')),
        'dewpoint_depression_f': None,
    }
    if current['high_so_far_f'] is None or current['latest_temp_f'] is None:
        return {}, 'MISSING_CAUSAL_SURFACE_STATE'
    pred = model.predict(current)
    if pred.median_residual_f is None:
        return {}, f'INSUFFICIENT_PRIOR_HISTORY:{pred.sample_count}'
    features: dict[str, object] = {
        'surface.high_so_far_f': current['high_so_far_f'],
        'surface.latest_temp_f': current['latest_temp_f'],
        'surface.minutes_since_high': current['minutes_since_high'],
        'surface.drop_from_high_f': current['drop_from_high_f'],
        'surface.slope_15m_f_per_min': current['slope_15m_f_per_min'],
        'forecast.remaining_heating_history_n': pred.sample_count,
        'forecast.remaining_heating_terminal_probability': pred.terminal_probability,
        'forecast.remaining_heating_final_high_samples_f': list(pred.final_high_samples(float(current['high_so_far_f']))),
        'market.settlement_source_family': str(row.get('source_family') or 'UNKNOWN'),
    }
    return features, 'OK'


def run_cycle(args: argparse.Namespace, model: EmpiricalRemainingHeatingModel, store: StateStore, state: dict[str, Any]) -> None:
    snap_text, rows = latest_snapshot(args.contract_history)
    if not snap_text or not rows:
        print('v2_shadow no_snapshot', flush=True); return
    snap = datetime.fromisoformat(snap_text.replace('Z', '+00:00')).astimezone(timezone.utc)
    age = (datetime.now(timezone.utc) - snap).total_seconds()
    if age > args.max_snapshot_age_seconds:
        print(f'v2_shadow stale_snapshot age_s={age:.1f}', flush=True); return
    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event = str(row.get('event_id') or '')
        if event:
            by_event.setdefault(event, []).append(row)
    strategy = EmpiricalRemainingHeatingStrategy(min_edge=args.min_edge, min_samples=args.min_samples)
    decisions = 0; eligible_events = 0
    for event, group in sorted(by_event.items()):
        representative = group[0]
        features, feature_status = current_features(representative, snap, model)
        candidates = []
        if feature_status == 'OK':
            for row in group:
                market = MarketSnapshot(
                    timestamp=snap, venue='kalshi', contract_id=str(row['ticker']), weather_event_id=event,
                    yes_bid=D(row.get('yes_bid')), yes_ask=D(row.get('yes_ask')),
                    no_bid=D(row.get('no_bid')), no_ask=D(row.get('no_ask')),
                    metadata=strike_metadata(row),
                )
                signals, evaluation = strategy.evaluate(market, features)
                candidates.append((row, market, signals[0] if signals else None, evaluation))
        else:
            append_jsonl(args.decisions_output, {'snapshot_time': snap.isoformat(), 'event_id': event,
                                                 'decision': feature_status, 'strategy': strategy.name})
            continue
        eligible = [x for x in candidates if x[2] is not None]
        best = max(eligible, key=lambda x: x[2].executable_edge) if eligible else None
        for row, market, signal, evaluation in candidates:
            is_best = best is not None and signal is best[2]
            if evaluation.emitted and not is_best:
                evaluation = replace(evaluation, emitted=False,
                                     reason=(evaluation.reason or '') + '; EVENT_LEVEL_LOWER_EDGE_CANDIDATE')
            store.evaluation(evaluation)
            decisions += 1
        if best is None:
            continue
        eligible_events += 1
        row, market, signal, evaluation = best
        assert signal is not None
        already = event in state.setdefault('signaled_events', {})
        detail = {
            'snapshot_time': snap.isoformat(), 'strategy': strategy.name, 'event_id': event,
            'ticker': signal.contract_id, 'side': signal.side.value,
            'model_probability': signal.model_probability, 'entry_ask': signal.executable_price,
            'gross_edge': signal.executable_edge, 'station': row.get('station'),
            'settlement_date': row.get('settlement_date'),
            'high_so_far_f': features['surface.high_so_far_f'],
            'latest_temp_f': features['surface.latest_temp_f'],
            'minutes_since_high': features['surface.minutes_since_high'],
            'drop_from_high_f': features['surface.drop_from_high_f'],
            'history_n': features['forecast.remaining_heating_history_n'],
            'terminal_probability': features['forecast.remaining_heating_terminal_probability'],
            'reasoning': signal.metadata.get('reasoning'),
            'decision': 'ALREADY_SIGNALED_EVENT' if already else 'SHADOW_SIGNAL',
            'live_order_submission': False,
        }
        append_jsonl(args.decisions_output, detail)
        if already:
            continue
        store.signal(signal)
        append_jsonl(args.signals_output, detail)
        state['signaled_events'][event] = {'ticker': signal.contract_id, 'signal_time': snap.isoformat()}
        print('V2_SHADOW_SIGNAL ' + json.dumps(detail, sort_keys=True), flush=True)
    save_state(args.state, state)
    print(f'v2_shadow snapshot={snap.isoformat()} events={len(by_event)} decisions={decisions} eligible_events={eligible_events} '
          f'total_signaled={len(state.get("signaled_events", {}))}', flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description='Live read-only empirical remaining-heating shadow signal monitor')
    p.add_argument('--history', type=Path, default=Path('/data/weather/normalized/features/remaining_heating_history.parquet'))
    p.add_argument('--contract-history', type=Path, default=Path('/data/weather/live/weather_company_contract_history.jsonl'))
    p.add_argument('--db', type=Path, default=Path('/data/weather/live/weather_research.sqlite3'))
    p.add_argument('--state', type=Path, default=Path('/data/weather/live/remaining_heating_shadow_state.json'))
    p.add_argument('--signals-output', type=Path, default=Path('/data/weather/live/remaining_heating_shadow_signals.jsonl'))
    p.add_argument('--decisions-output', type=Path, default=Path('/data/weather/live/remaining_heating_shadow_decisions.jsonl'))
    p.add_argument('--loop-seconds', type=int, default=60)
    p.add_argument('--max-snapshot-age-seconds', type=int, default=180)
    p.add_argument('--min-edge', type=float, default=0.05)
    p.add_argument('--min-samples', type=int, default=30)
    p.add_argument('--k', type=int, default=80)
    p.add_argument('--once', action='store_true')
    args = p.parse_args()
    history = pd.read_parquet(args.history).to_dict('records')
    model = EmpiricalRemainingHeatingModel(history, k=args.k, min_samples=args.min_samples)
    store = StateStore(args.db)
    state = load_state(args.state)
    print('mode=TERMINAL_HIGH_EMPIRICAL_V2_SHADOW live_order_submission=false', flush=True)
    while True:
        try:
            run_cycle(args, model, store, state)
            if args.once:
                break
        except KeyboardInterrupt:
            save_state(args.state, state); raise
        except Exception as exc:
            print(f'v2_shadow_error={type(exc).__name__}:{exc}', flush=True)
        time.sleep(max(10, args.loop_seconds))


if __name__ == '__main__':
    raise SystemExit(main())
