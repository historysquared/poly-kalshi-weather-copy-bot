from scripts.run_live_alpha_shadow import input_snapshot_key, ladder_signals, load_live_bbo, monotonic_signals


def base_row(shape, ticker, yes_ask, no_ask, lower=None, upper=None, high=80.0):
    return {
        "snapshot_time": "2026-09-28T12:00:00+00:00",
        "event_id": "EVT", "ticker": ticker, "station": "KXYZ", "settlement_date": "2026-09-28",
        "shape": shape, "lower": lower, "upper": upper, "high_so_far_f": high,
        "yes_ask": yes_ask, "no_ask": no_ask, "estimated_taker_fee_per_contract": 0.0,
        "source_family": "WEATHER_COMPANY",
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
    assert run_cycle(args,{},health)==1
    # The independent ladder-semantic blocker remains for the next bounded fix.


@pytest.mark.parametrize('value',['bad','2026-10-12T01:00:00'])
def test_invalid_envelope_timestamp_abstains_with_health(tmp_path,value):
    _,_,payload,args,health=_cycle_fixture(tmp_path)
    payload['generated_at']=value;args.bbo.write_text(json.dumps(payload))
    assert run_cycle(args,{},health)==0
    assert json.loads(health.status_path.read_text())['reason']=='INVALID_INPUT_TIMESTAMP'
