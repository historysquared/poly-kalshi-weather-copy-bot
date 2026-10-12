from types import SimpleNamespace
import json
from scripts.run_weather_company_dashboard import write_outputs
from scripts.run_live_alpha_shadow import latest_snapshot
from weather_alpha.ops.health import StrategyHealth

def args_at(root,current=True):
    return SimpleNamespace(current_only=current,**{key:root/name for key,name in {
        "json_output":"current.json","csv_output":"events.csv","contract_csv_output":"contracts.csv",
        "text_output":"current.txt","snapshot_history":"snapshots.jsonl",
        "contract_snapshot_history":"contracts.jsonl"}.items()})

def snap(stamp,tickers):
    return {"generated_at":stamp,"rows":[],"contract_rows":[{"ticker":t,"snapshot_time":stamp} for t in tickers],
      "counts":{"contracts":len(tickers),"events":0,"eligible":0,"pending":0,"traded_events":0,"audited_contract_quotes":len(tickers)}}

def test_current_only_removes_departed_contracts_and_remains_consumable(tmp_path):
    a=args_at(tmp_path)
    write_outputs(a,snap("2026-10-12T05:00:00+00:00",["OLD","KEEP"]))
    write_outputs(a,snap("2026-10-12T05:01:00+00:00",["KEEP"]))
    stamp,rows=latest_snapshot(a.contract_snapshot_history)
    assert stamp=="2026-10-12T05:01:00+00:00" and [r["ticker"] for r in rows]==["KEEP"]
    assert "OLD" not in a.contract_snapshot_history.read_text()
    assert len(a.snapshot_history.read_text().splitlines())==1

def test_empty_current_snapshot_clears_previous_enrichment(tmp_path):
    a=args_at(tmp_path)
    write_outputs(a,snap("2026-10-12T05:00:00+00:00",["OLD"]))
    write_outputs(a,snap("2026-10-12T05:01:00+00:00",[]))
    assert latest_snapshot(a.contract_snapshot_history)==(None,[])

def test_legacy_history_mode_preserves_multiple_snapshots(tmp_path):
    a=args_at(tmp_path,False)
    for stamp in ["2026-10-12T05:00:00+00:00","2026-10-12T05:01:00+00:00"]:
        write_outputs(a,snap(stamp,["KEEP"]))
    assert len(a.snapshot_history.read_text().splitlines())==2

def test_health_can_be_isolated_from_legacy_paths(tmp_path):
    status=tmp_path/"status";events=tmp_path/"health.jsonl"
    h=StrategyHealth("weather_company_dashboard",status_dir=status,events_path=events)
    h.report("OK","CYCLE_OK")
    assert json.loads((status/"weather_company_dashboard.json").read_text())["reason"]=="CYCLE_OK"
    assert events.exists()
