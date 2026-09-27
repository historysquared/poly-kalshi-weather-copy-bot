#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from weather_alpha.backtest.causal_trade_backtest import D, executable_buy, kalshi_fee, parse_dt, terminal_pnl
from weather_alpha.backtest.validation import EventReturn
from weather_alpha.research.remaining_heating import EmpiricalRemainingHeatingModel, empirical_bucket_probabilities
from weather_alpha.research.scorecard import score_event_returns

REPLAY_COLUMNS = ['contract_id', 'book_time', 'yes_bids', 'no_bids', 'clock_source']


def parse_ints(value: str) -> list[int]:
    return [int(x) for x in value.split(',') if x.strip()]


def parse_decimals(value: str) -> list[Decimal]:
    return [Decimal(x.strip()) for x in value.split(',') if x.strip()]


def contract_yes(contract: dict[str, Any], value_f: Decimal) -> bool:
    shape = str(contract.get('shape') or '').lower()
    lo = D(contract.get('lower'))
    hi = D(contract.get('upper'))
    if shape == 'above':
        return lo is not None and value_f > lo
    if shape == 'below':
        return hi is not None and value_f < hi
    if shape == 'bucket':
        return lo is not None and hi is not None and lo <= value_f <= hi
    raise ValueError(f'unsupported contract shape {shape!r}')


def as_empirical_contract(row: dict[str, Any]) -> dict[str, Any]:
    shape = str(row.get('shape') or '').lower()
    strike = {'bucket': 'between', 'below': 'less', 'above': 'greater'}.get(shape, shape)
    return {'ticker': row['contract_id'], 'strike_type': strike,
            'floor_strike': row.get('lower'), 'cap_strike': row.get('upper')}


def capture_targets(replay: Path, targets: dict[str, list[tuple[str, datetime]]], *, batch_size: int) -> dict[tuple[str, str], dict[str, Any]]:
    if not targets:
        return {}
    ticker_values = pa.array(sorted(targets), type=pa.string())
    captured: dict[tuple[str, str], dict[str, Any]] = {}
    pf = pq.ParquetFile(replay)
    scanned = relevant = 0
    for bi, batch in enumerate(pf.iter_batches(columns=REPLAY_COLUMNS, batch_size=batch_size), 1):
        scanned += batch.num_rows
        mask = pc.is_in(batch.column(batch.schema.get_field_index('contract_id')), value_set=ticker_values)
        filtered = batch.filter(mask)
        relevant += filtered.num_rows
        for snap in filtered.to_pylist():
            ticker = str(snap.get('contract_id') or '')
            ts = parse_dt(snap['book_time'])
            for label, target in targets.get(ticker, []):
                if ts < target:
                    continue
                key = (ticker, label)
                current = captured.get(key)
                if current is None or ts < parse_dt(current['book_time']):
                    captured[key] = snap
        if bi % 250 == 0:
            print(f'replay_capture batches={bi} scanned={scanned} relevant={relevant} captured={len(captured)}', flush=True)
    print(f'replay_capture_done scanned={scanned} relevant={relevant} captured={len(captured)}', flush=True)
    return captured


def main() -> int:
    p = argparse.ArgumentParser(description='Economic backtest of empirical remaining-heating bucket strategy')
    p.add_argument('--history', type=Path, default=Path('/data/weather/normalized/features/remaining_heating_history.parquet'))
    p.add_argument('--catalog', type=Path, default=Path('/data/weather/normalized/markets/kalshi_weather_catalog_bulk_resolved.parquet'))
    p.add_argument('--cli', type=Path, default=Path('/data/weather/normalized/settlements/nws_cli_daily.parquet'))
    p.add_argument('--replay', type=Path, default=Path('/data/weather/normalized/orderbooks/kalshi_weather_pmxt_bulk.parquet'))
    p.add_argument('--start-date', default='2026-05-14')
    p.add_argument('--end-date', default='2026-06-10')
    p.add_argument('--hours', default='14,15,16,17,18')
    p.add_argument('--k', type=int, default=80)
    p.add_argument('--min-samples', type=int, default=30)
    p.add_argument('--min-net-edge', type=Decimal, default=Decimal('0.05'))
    p.add_argument('--signal-price-floor', type=Decimal, default=Decimal('0.10'))
    p.add_argument('--max-decision-delay-seconds', type=int, default=180)
    p.add_argument('--latencies', default='0,30,60,120,300')
    p.add_argument('--depths', default='1,5')
    p.add_argument('--taker-fee-coefficient', type=Decimal, default=Decimal('0.07'))
    p.add_argument('--batch-size', type=int, default=20000)
    p.add_argument('--output', type=Path, default=Path('/data/weather/results/remaining_heating/empirical_trade_backtest.parquet'))
    p.add_argument('--summary-output', type=Path, default=Path('/data/weather/results/remaining_heating/empirical_trade_backtest_summary.parquet'))
    args = p.parse_args()

    hist = pd.read_parquet(args.history)
    hist['settlement_date'] = hist['settlement_date'].astype(str).str[:10]
    model = EmpiricalRemainingHeatingModel(hist.to_dict('records'), k=args.k, min_samples=args.min_samples)
    hours = sorted(set(parse_ints(args.hours)))

    catalog_df = pd.read_parquet(args.catalog)
    catalog_df['settlement_date'] = catalog_df['settlement_date'].astype(str).str[:10]
    source_text = catalog_df['settlement_source'].fillna('').astype(str).str.lower()
    nws_source = source_text.str.contains('national weather service|climatological report|daily climate report|nws', regex=True)
    source_rejected = int(((catalog_df.status == 'EXACT') & ~nws_source).sum())
    catalog_df = catalog_df[(catalog_df.status == 'EXACT') & nws_source &
                            (catalog_df.settlement_date >= args.start_date) &
                            (catalog_df.settlement_date <= args.end_date)]
    print(f'nws_cli_catalog_contracts={len(catalog_df)} source_mismatch_exact_contracts={source_rejected}', flush=True)
    by_event: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in catalog_df.to_dict('records'):
        by_event[(str(row['station']), str(row['settlement_date']))].append(row)

    cli_df = pd.read_parquet(args.cli)
    cli = {(str(r.station), str(r.valid_date)[:10]): Decimal(str(r.high_f))
           for r in cli_df.itertuples(index=False) if not pd.isna(r.high_f)}

    state_rows = hist[(hist.settlement_date >= args.start_date) & (hist.settlement_date <= args.end_date) &
                      hist.snapshot_hour_lst.isin(hours)].to_dict('records')
    state_by_event_hour = {(str(r['station']), str(r['settlement_date']), int(r['snapshot_hour_lst'])): r for r in state_rows}

    # First replay pass: capture the first executable book at/after each physical decision snapshot.
    decision_targets: dict[str, list[tuple[str, datetime]]] = defaultdict(list)
    probability_cache: dict[tuple[str, str, int], dict[str, float]] = {}
    prediction_meta: dict[tuple[str, str, int], dict[str, Any]] = {}
    for (station, day), contracts in sorted(by_event.items()):
        for hour in hours:
            state = state_by_event_hour.get((station, day, hour))
            if state is None:
                continue
            pred = model.predict(state)
            if pred.median_residual_f is None:
                continue
            empirical_contracts = [as_empirical_contract(c) for c in contracts]
            probs = empirical_bucket_probabilities(empirical_contracts, pred.final_high_samples(float(state['high_so_far_f'])))
            key = (station, day, hour)
            probability_cache[key] = probs
            prediction_meta[key] = {'sample_count': pred.sample_count, 'terminal_probability': pred.terminal_probability,
                                    'median_residual_f': pred.median_residual_f, 'high_so_far_f': float(state['high_so_far_f'])}
            target = parse_dt(state['snapshot_time_utc'])
            label = f'{station}|{day}|{hour}'
            for contract in contracts:
                decision_targets[str(contract['contract_id'])].append((label, target))
    decision_books = capture_targets(args.replay, decision_targets, batch_size=args.batch_size)

    selected: list[dict[str, Any]] = []
    for (station, day), contracts in sorted(by_event.items()):
        chosen = None
        for hour in hours:
            key = (station, day, hour)
            probs = probability_cache.get(key)
            if not probs:
                continue
            label = f'{station}|{day}|{hour}'
            target = parse_dt(state_by_event_hour[(station, day, hour)]['snapshot_time_utc'])
            candidates = []
            for contract in contracts:
                ticker = str(contract['contract_id'])
                snap = decision_books.get((ticker, label))
                if snap is None:
                    continue
                delay = (parse_dt(snap['book_time']) - target).total_seconds()
                if delay < 0 or delay > args.max_decision_delay_seconds:
                    continue
                p_yes = Decimal(str(probs.get(ticker, 0.0)))
                for side, p_side in [('YES', p_yes), ('NO', Decimal('1') - p_yes)]:
                    fill = executable_buy(snap, side, Decimal('1'))
                    if not fill.complete or fill.vwap is None:
                        continue
                    ask = fill.vwap
                    fee = kalshi_fee(coefficient=args.taker_fee_coefficient, contracts=Decimal('1'), price=ask)
                    net_edge = p_side - ask - fee
                    candidates.append((net_edge, side, ticker, ask, p_side, fee, snap, contract, delay))
            if not candidates:
                continue
            best = max(candidates, key=lambda x: x[0])
            net_edge, side, ticker, ask, p_side, fee, snap, contract, delay = best
            if ask < args.signal_price_floor or net_edge < args.min_net_edge:
                continue
            meta = prediction_meta[key]
            chosen = {
                'station': station, 'settlement_date': day, 'snapshot_hour_lst': hour,
                'contract_id': ticker, 'side': side, 'signal_time': snap['book_time'],
                'decision_target_time': target.isoformat(), 'decision_delay_seconds': delay,
                'model_probability_side': str(p_side), 'signal_price': str(ask),
                'signal_fee_1_contract': str(fee), 'signal_net_edge': str(net_edge),
                'sample_count': meta['sample_count'], 'terminal_probability': meta['terminal_probability'],
                'median_residual_f': meta['median_residual_f'], 'high_so_far_f': meta['high_so_far_f'],
                'shape': contract.get('shape'), 'lower': contract.get('lower'), 'upper': contract.get('upper'),
            }
            break  # one first-qualified signal per physical station-day event
        if chosen:
            selected.append(chosen)
    print(f'physical_events={len(by_event)} selected_signals={len(selected)}', flush=True)

    # Second replay pass: execution after explicit latency.
    latencies = parse_ints(args.latencies)
    depths = parse_decimals(args.depths)
    exec_targets: dict[str, list[tuple[str, datetime]]] = defaultdict(list)
    for sig in selected:
        st = parse_dt(sig['signal_time'])
        for latency in latencies:
            exec_targets[sig['contract_id']].append((f"{sig['station']}|{sig['settlement_date']}|{latency}", st + timedelta(seconds=latency)))
    exec_books = capture_targets(args.replay, exec_targets, batch_size=args.batch_size)

    catalog_map = {str(r['contract_id']): r for r in catalog_df.to_dict('records')}
    trades: list[dict[str, Any]] = []
    for sig in selected:
        ticker = sig['contract_id']; contract = catalog_map[ticker]
        official_high = cli.get((sig['station'], sig['settlement_date']))
        if official_high is None:
            continue
        official_yes = contract_yes(contract, official_high)
        for latency in latencies:
            snap = exec_books.get((ticker, f"{sig['station']}|{sig['settlement_date']}|{latency}"))
            for depth in depths:
                row = {**sig, 'latency_seconds': latency, 'depth_contracts': str(depth),
                       'official_cli_high_f': str(official_high), 'official_yes_winner': official_yes,
                       'fee_coefficient': str(args.taker_fee_coefficient)}
                if snap is None:
                    row['status'] = 'NO_POST_LATENCY_BOOK'; trades.append(row); continue
                fill = executable_buy(snap, sig['side'], depth)
                if not fill.complete or fill.vwap is None:
                    row['status'] = 'INSUFFICIENT_DEPTH'; trades.append(row); continue
                fee = kalshi_fee(coefficient=args.taker_fee_coefficient, contracts=depth, price=fill.vwap)
                pnl = terminal_pnl(side=sig['side'], price=fill.vwap, contracts=depth,
                                   official_yes_winner=official_yes, fee=fee)
                capital = depth * fill.vwap + fee
                row.update({'status': 'EXECUTED', 'execution_time': snap['book_time'], 'execution_vwap': str(fill.vwap),
                            'filled_contracts': str(fill.filled), 'fee': str(fee), 'pnl': str(pnl),
                            'capital_at_risk': str(capital), 'roi': None if capital == 0 else str(pnl / capital),
                            'won': official_yes if sig['side'] == 'YES' else not official_yes})
                trades.append(row)

    summaries: list[dict[str, Any]] = []
    for latency in latencies:
        for depth in depths:
            rows = [r for r in trades if r['latency_seconds'] == latency and r['depth_contracts'] == str(depth) and r.get('status') == 'EXECUTED']
            events = [EventReturn(strategy='terminal_high_empirical_v2', event_id=f"{r['station']}|{r['settlement_date']}",
                                  date=r['settlement_date'], station=r['station'], latency_seconds=latency,
                                  execution_case=f'DEPTH_{depth}', pnl=float(r['pnl']), capital=float(r['capital_at_risk'])) for r in rows]
            card = score_event_returns('terminal_high_empirical_v2', events)
            summary = card.to_dict()
            summary['mean_markout_by_horizon_json'] = json.dumps(summary.pop('mean_markout_by_horizon', {}), sort_keys=True)
            summaries.append({**summary, 'latency_seconds': latency, 'depth_contracts': str(depth),
                              'selected_signals': len(selected), 'executed_trades': len(rows)})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(trades) if trades else pa.table({'status': pa.array([], type=pa.string())}), args.output, compression='zstd')
    pq.write_table(pa.Table.from_pylist(summaries), args.summary_output, compression='zstd')
    for row in summaries:
        print('summary=' + repr(row))
    print(f'trade_output={args.output}\nsummary_output={args.summary_output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
