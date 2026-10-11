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
