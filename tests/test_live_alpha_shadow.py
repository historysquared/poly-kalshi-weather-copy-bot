from scripts.run_live_alpha_shadow import input_snapshot_key, ladder_signals, load_live_bbo, monotonic_signals


def base_row(shape, ticker, yes_ask, no_ask, lower=None, upper=None, high=80.0):
    return {
        "snapshot_time": "2026-09-28T12:00:00+00:00",
        "event_id": "EVT", "ticker": ticker, "station": "KXYZ", "settlement_date": "2026-09-28",
        "shape": shape, "lower": lower, "upper": upper, "high_so_far_f": high,
        "yes_ask": yes_ask, "no_ask": no_ask, "estimated_taker_fee_per_contract": 0.0,
        "source_family": "WEATHER_COMPANY",
        "event_contract_ids": ["A", "B", "C"],
        "settlement_semantics": {
            "mapping_status": "EXACT", "evidence": "verified synthetic test fixture",
            "source": "TWC", "station": "KXYZ", "date": "2026-09-28",
            "measurement": "DAILY_HIGH", "window": "verified-window", "unit": "F",
            "rounding": "WHOLE_DEGREE", "value_domain": "INTEGER",
            "lower_inclusive": shape == "bucket", "upper_inclusive": shape == "bucket",
        },
    }


def test_yes_ladder_underround_emits_locked_basket():
    rows = [
        base_row("below", "A", 0.20, 0.81, upper=70),
        base_row("bucket", "B", 0.25, 0.76, lower=70, upper=75),
        base_row("above", "C", 0.45, 0.56, lower=75),
    ]
    signals = ladder_signals(rows, rows[0]["snapshot_time"], 0.02)
    yes = [x for x in signals if x["strategy"] == "LADDER_YES_UNDERROUND_V1"]
    assert len(yes) == 1
    assert round(yes[0]["net_edge_after_fee"], 6) == 0.10


def test_incomplete_ladder_does_not_emit():
    rows = [base_row("below", "A", 0.2, 0.8, upper=70), base_row("above", "C", 0.7, 0.3, lower=75)]
    assert ladder_signals(rows, rows[0]["snapshot_time"], 0.01) == []


def test_monotonic_bucket_elimination_requires_buffer_and_price_gap():
    row = base_row("bucket", "B", 0.2, 0.85, lower=70, upper=75, high=77.2)
    signals = monotonic_signals([row], row["snapshot_time"], 2.0, 0.95, 0.03)
    assert len(signals) == 1
    assert signals[0]["side"] == "NO"
    assert signals[0]["strategy"] == "MONOTONIC_HIGH_LOCK_V1"
    row["high_so_far_f"] = 76.9
    assert monotonic_signals([row], row["snapshot_time"], 2.0, 0.95, 0.03) == []


def test_input_snapshot_key_changes_for_any_source():
    base = input_snapshot_key("catalog-1", "bbo-1", "enriched-1")
    assert input_snapshot_key("catalog-1", "bbo-1", "enriched-1") == base
    assert input_snapshot_key("catalog-2", "bbo-1", "enriched-1") != base
    assert input_snapshot_key("catalog-1", "bbo-2", "enriched-1") != base
    assert input_snapshot_key("catalog-1", "bbo-1", "enriched-2") != base


def test_load_live_bbo(tmp_path):
    path = tmp_path / "bbo.json"
    path.write_text(
        '{"generated_at":"2026-10-11T17:00:00+00:00","quotes":{"A":{"yes_ask":"0.40"},"B":null}}'
    )
    generated, quotes = load_live_bbo(path)
    assert generated == "2026-10-11T17:00:00+00:00"
    assert quotes == {"A": {"yes_ask": "0.40"}}


import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from scripts.run_live_alpha_shadow import quote_is_fresh, run_cycle
from weather_alpha.ops.health import StrategyHealth


@pytest.mark.parametrize('timestamp, expected', [
    ('2026-10-12T01:00:00Z', True),
    ('2026-10-12T00:59:30+00:00', True),
    ('2026-10-12T00:59:29+00:00', False),
    ('2026-10-11T01:00:00+00:00', False),
    ('2026-10-12T01:00:01+00:00', False),
    ('2026-10-12T01:00:00', False),
    ('garbage', False),
    (None, False),
    ('2026-10-11T18:00:00-07:00', True),
])
def test_individual_quote_freshness(timestamp, expected):
    now = datetime(2026, 10, 12, 1, tzinfo=timezone.utc)
    assert quote_is_fresh({'local_receive_timestamp': timestamp}, now, 30) is expected


def _cycle_fixture(tmp_path):
    now = datetime.now(timezone.utc)
    tickers=['KXHIGHNY-26OCT11-T60', 'KXHIGHNY-26OCT11-B70.5', 'KXHIGHNY-26OCT11-T80']
    markets=[{'ticker':ticker,'event_ticker':'E','strike_type':shape,'floor_strike':lo,'cap_strike':hi}
             for ticker,shape,lo,hi in zip(tickers,['less','between','greater'],[None,70,80],[60,71,None])]
    (tmp_path/'catalog').write_text(json.dumps({'generated_at':now.isoformat(),'active_series':[{'ticker':'KXHIGHNY','markets':markets}]}))
    payload={'generated_at':now.isoformat(),'quotes':{ticker:{'yes_ask':'0.1','no_ask':'0.9','local_receive_timestamp':now.isoformat()} for ticker in tickers}}
    args=SimpleNamespace(catalog=tmp_path/'catalog', bbo=tmp_path/'bbo', contract_history=tmp_path/'missing',
        max_catalog_age_seconds=25200,max_snapshot_age_seconds=30,max_surface_age_seconds=180,
        minimum_event_coverage=1,min_locked_edge=.02,basis_margin_f=2,max_lock_price=.95,
        min_lock_price_gap=.03,signals_output=tmp_path/'signals',state=tmp_path/'state')
    health=StrategyHealth('test',status_dir=tmp_path/'health',events_path=tmp_path/'events')
    return now,tickers,payload,args,health


@pytest.mark.parametrize('bad', ['stale','future','missing','naive','malformed','absent_leg'])
def test_fresh_envelope_cannot_hide_invalid_individual_leg(tmp_path,bad):
    now,tickers,payload,args,health=_cycle_fixture(tmp_path)
    q=payload['quotes'][tickers[1]]
    if bad=='stale': q['local_receive_timestamp']=(now-timedelta(days=1)).isoformat()
    elif bad=='future': q['local_receive_timestamp']=(now+timedelta(minutes=1)).isoformat()
    elif bad=='missing': q.pop('local_receive_timestamp')
    elif bad=='naive': q['local_receive_timestamp']=now.replace(tzinfo=None).isoformat()
    elif bad=='malformed': q['local_receive_timestamp']='bad'
    else: del payload['quotes'][tickers[1]]
    args.bbo.write_text(json.dumps(payload))
    assert run_cycle(args,{},health)==0
    assert not args.signals_output.exists()
    status=json.loads(health.status_path.read_text())
    assert status['invalid_quote_events']==1


def test_fresh_individual_quotes_still_reach_strategy(tmp_path):
    _,_,payload,args,health=_cycle_fixture(tmp_path)
    args.bbo.write_text(json.dumps(payload))
    assert run_cycle(args,{},health)==0
    # Fresh quotes are eligible, but unverified settlement metadata blocks baskets.
    assert json.loads(health.status_path.read_text())["invalid_quote_events"] == 0


@pytest.mark.parametrize('value',['bad','2026-10-12T01:00:00'])
def test_invalid_envelope_timestamp_abstains_with_health(tmp_path,value):
    _,_,payload,args,health=_cycle_fixture(tmp_path)
    payload['generated_at']=value;args.bbo.write_text(json.dumps(payload))
    assert run_cycle(args,{},health)==0
    assert json.loads(health.status_path.read_text())['reason']=='INVALID_INPUT_TIMESTAMP'


from copy import deepcopy
from scripts.run_live_alpha_shadow import complete_ladder, ladder_validation_reason, catalog_snapshot


def _verified_ladder():
    return [base_row("below", "A", .2, .1, upper=70),
            base_row("bucket", "B", .2, .1, lower=70, upper=75),
            base_row("above", "C", .2, .1, lower=75)]


def test_verified_integer_partition_yes_and_no_payouts():
    rows = _verified_ladder()
    assert complete_ladder(rows)
    signals = ladder_signals(rows, "test", .02)
    assert {x["side"] for x in signals} == {"BASKET_YES", "BASKET_NO"}
    assert next(x for x in signals if x["side"] == "BASKET_NO")["net_edge_after_fee"] == pytest.approx(1.7)
    assert complete_ladder(list(reversed(rows)))


@pytest.mark.parametrize("fault", ["gap", "overlap", "duplicate_ticker", "missing_leg", "extra_leg",
    "mixed_event", "mixed_station", "mixed_date", "mixed_source", "mixed_measurement", "mixed_window",
    "unknown_rounding", "no_evidence", "unverified", "missing_semantics", "missing_inclusivity",
    "noninteger", "nan_bound", "reversed", "missing_tail", "missing_membership", "different_membership"])
def test_invalid_ladder_never_emits(fault):
    rows = _verified_ladder()
    if fault == "gap": rows[1]["lower"] = 71
    elif fault == "overlap": rows[1]["lower"] = 69
    elif fault == "duplicate_ticker": rows[1]["ticker"] = "A"
    elif fault == "missing_leg": rows.pop(1)
    elif fault == "extra_leg": rows.append(deepcopy(rows[1])); rows[-1]["ticker"] = "D"
    elif fault == "mixed_event": rows[1]["event_id"] = "OTHER"
    elif fault.startswith("mixed_"): rows[1]["settlement_semantics"][fault[6:]] = "OTHER"
    elif fault == "unknown_rounding": rows[1]["settlement_semantics"]["rounding"] = "UNKNOWN"
    elif fault == "no_evidence": rows[1]["settlement_semantics"]["evidence"] = ""
    elif fault == "unverified": rows[1]["settlement_semantics"]["mapping_status"] = "REVIEW"
    elif fault == "missing_semantics": rows[1].pop("settlement_semantics")
    elif fault == "missing_inclusivity": rows[1]["settlement_semantics"].pop("lower_inclusive")
    elif fault == "noninteger": rows[1]["lower"] = 70.5
    elif fault == "nan_bound": rows[1]["lower"] = "NaN"
    elif fault == "reversed": rows[1]["lower"] = 76
    elif fault == "missing_tail": rows[0]["shape"] = "bucket"; rows[0]["lower"] = 0
    elif fault == "missing_membership": rows[1].pop("event_contract_ids")
    elif fault == "different_membership": rows[1]["event_contract_ids"] = ["A", "B", "D"]
    assert not complete_ladder(rows)
    assert ladder_signals(rows, "test", .01) == []


@pytest.mark.parametrize("left_inclusive,right_inclusive,expected", [(True,False,True),(False,True,True),(True,True,False),(False,False,False)])
def test_continuous_boundary_requires_exactly_one_owner(left_inclusive,right_inclusive,expected):
    rows = _verified_ladder()
    for row in rows:
        row["settlement_semantics"].update(value_domain="CONTINUOUS",rounding="NONE")
    rows[0]["settlement_semantics"]["upper_inclusive"] = left_inclusive
    rows[1]["settlement_semantics"]["lower_inclusive"] = right_inclusive
    rows[1]["settlement_semantics"]["upper_inclusive"] = True
    rows[2]["settlement_semantics"]["lower_inclusive"] = False
    assert complete_ladder(rows) is expected


@pytest.mark.parametrize("price", [None, float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_price_cannot_generate_basket_side(price):
    rows = _verified_ladder(); rows[1]["yes_ask"] = price
    assert not any(x["side"] == "BASKET_YES" for x in ladder_signals(rows, "test", .01))


def test_catalog_carries_full_membership_without_inventing_semantics(tmp_path):
    _,tickers,_,args,_ = _cycle_fixture(tmp_path)
    _,rows = catalog_snapshot(args.catalog)
    assert all(row["event_contract_ids"] == tickers for row in rows)
    assert all(row["settlement_semantics"] is None for row in rows)
    assert not complete_ladder(rows)


def test_verified_catalog_event_reaches_ladder_and_missing_quote_cannot_reduce_it(tmp_path):
    _,tickers,payload,args,health = _cycle_fixture(tmp_path)
    catalog = json.loads(args.catalog.read_text())
    markets = catalog['active_series'][0]['markets']
    verified = _verified_ladder()
    for market, row in zip(markets,verified):
        market['floor_strike']=row['lower'];market['cap_strike']=row['upper']
        market['settlement_semantics']=deepcopy(row['settlement_semantics'])
    args.catalog.write_text(json.dumps(catalog));args.bbo.write_text(json.dumps(payload))
    assert run_cycle(args,{},health)==1
    assert json.loads(health.status_path.read_text())['ladder_validation_failures']=={}
    # Removing any leg's quote must reject the whole canonical event.
    args.signals_output.unlink();del payload['quotes'][tickers[1]]
    args.bbo.write_text(json.dumps(payload))
    assert run_cycle(args,{},health)==0
    assert not args.signals_output.exists()
