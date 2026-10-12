import json

from weather_alpha.ops.health import StrategyHealth


def test_strategy_health_persists_current_and_emits_only_transitions(tmp_path):
    status_dir = tmp_path / "status"
    events = tmp_path / "events.jsonl"
    health = StrategyHealth("demo", status_dir=status_dir, events_path=events)
    health.report("ERROR", "NO_DATA", station="KNYC")
    health.report("ERROR", "NO_DATA", station="KNYC")
    health.report("OK", "CYCLE_OK")
    current = json.loads((status_dir / "demo.json").read_text())
    assert current["status"] == "OK"
    rows = [json.loads(x) for x in events.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["status"] == "ERROR"
    assert rows[1]["previous_status"] == "ERROR"


from datetime import datetime, timedelta, timezone
import pytest
from weather_alpha.ops.health import assess_health, health_alert_transitions

NOW = datetime(2026,10,12,2,tzinfo=timezone.utc)


@pytest.mark.parametrize('timestamp, reason', [
    ((NOW-timedelta(seconds=181)).isoformat(),'STALE_HEALTH'),
    ((NOW+timedelta(seconds=1)).isoformat(),'INVALID_HEALTH_TIMESTAMP'),
    ('bad','INVALID_HEALTH_TIMESTAMP'), (None,'INVALID_HEALTH_TIMESTAMP'),
    (NOW.replace(tzinfo=None).isoformat(),'INVALID_HEALTH_TIMESTAMP'),
])
def test_health_cannot_keep_old_or_invalid_ok(timestamp,reason):
    view=assess_health({'status':'OK','reason':'CYCLE_OK','updated_at':timestamp},now=NOW)
    assert view['status']=='ERROR' and view['reason']==reason
    assert view['reported_status']=='OK'


def test_scheduled_history_uses_daily_freshness_budget():
    row={'component':'contrail_hourly_history','status':'OK','updated_at':(NOW-timedelta(hours=26)).isoformat()}
    assert assess_health(row,now=NOW)['status']=='OK'
    row['updated_at']=(NOW-timedelta(hours=28)).isoformat()
    assert assess_health(row,now=NOW)['reason']=='STALE_HEALTH'


def test_collector_requires_fresh_messages_even_with_fresh_health_file():
    row={'generated_at':NOW.isoformat(),'last_message_at':(NOW-timedelta(seconds=31)).isoformat(),'last_error':None}
    view=assess_health(row,now=NOW,timestamp_keys=('generated_at','last_message_at'),max_age_seconds=30)
    assert view['reason']=='STALE_HEALTH'


def test_stale_health_alerts_once_recovers_and_detects_deleted_file(tmp_path):
    folder=tmp_path/'health';folder.mkdir();collector=tmp_path/'collector.json'
    collector.write_text(json.dumps({'generated_at':NOW.isoformat(),'last_message_at':NOW.isoformat()}))
    status=folder/'demo.json';status.write_text(json.dumps({'status':'OK','updated_at':(NOW-timedelta(minutes=4)).isoformat()}))
    events,seen=health_alert_transitions(folder,collector,{},now=NOW)
    assert len(events)==1 and events[0]['reason']=='STALE_HEALTH'
    assert health_alert_transitions(folder,collector,seen,now=NOW)[0]==[]
    status.write_text(json.dumps({'status':'OK','reason':'CYCLE_OK','updated_at':NOW.isoformat()}))
    events,seen=health_alert_transitions(folder,collector,seen,now=NOW)
    assert len(events)==1 and events[0]['previous_reason']=='STALE_HEALTH' and events[0]['status']=='OK'
    status.unlink();events,seen=health_alert_transitions(folder,collector,seen,now=NOW)
    assert len(events)==1 and events[0]['reason']=='MISSING_HEALTH'


def test_health_age_crosses_limit_without_file_change(tmp_path):
    folder=tmp_path/'health';folder.mkdir();collector=tmp_path/'collector.json'
    collector.write_text(json.dumps({'generated_at':NOW.isoformat(),'last_message_at':NOW.isoformat()}))
    events,seen=health_alert_transitions(folder,collector,{},now=NOW)
    assert events==[]
    events,_=health_alert_transitions(folder,collector,seen,now=NOW+timedelta(seconds=31))
    assert len(events)==1 and events[0]['reason']=='STALE_HEALTH'
