from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.build_contrail_hourly_history import result_exit_code
from scripts.refresh_contrail_hourly_history import parse_fetch_error_count
from scripts.run_contrail_q90_monitor import trim_seen


ROOT = Path(__file__).resolve().parents[1]


def test_partial_history_exit_policy():
    assert result_exit_code(0, 10, False) == 0
    assert result_exit_code(1, 10, False) == 2
    assert result_exit_code(1, 10, True) == 0
    assert result_exit_code(10, 10, True) == 2


def test_refresh_parses_fetch_error_count():
    assert parse_fetch_error_count("output=x rows=12 successful_fetches=11 fetch_errors=1") == 1
    assert parse_fetch_error_count("no summary") is None


def test_q90_seen_state_is_bounded():
    seen = {f"city|2026-01-{i:05d}": f"2026-01-{i:05d}T00:00:00Z" for i in range(20)}
    pruned = trim_seen(seen, 7)
    assert pruned == 13
    assert len(seen) == 7


def test_contrail_installer_dry_run_preserves_stayton_by_default():
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts/install_contrail_current_timer.sh"), "--dry-run"],
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "ROOT": "/opt/weather-alpha-strategy-release", "SOURCE_ROOT": str(ROOT)},
        capture_output=True, text=True, check=True,
    )
    assert "keeping existing Stayton-only timer/service unchanged" in proc.stdout
    assert "disable --now weather-contrails-stayton.timer" not in proc.stdout


def test_contrail_installer_requires_explicit_retirement():
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts/install_contrail_current_timer.sh"), "--dry-run", "--retire-stayton"],
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "ROOT": "/opt/weather-alpha-strategy-release", "SOURCE_ROOT": str(ROOT)},
        capture_output=True, text=True, check=True,
    )
    assert "disable --now weather-contrails-stayton.timer" in proc.stdout


def test_systemd_runtime_scripts_exist():
    for unit_name in (
        "weather-contrails-current.service",
        "weather-contrails-history.service",
        "weather-contrails-q90.service",
    ):
        text = (ROOT / "deploy/systemd" / unit_name).read_text()
        assert "WorkingDirectory=/opt/weather-alpha-strategy-release" in text
        exec_line = next(line for line in text.splitlines() if line.startswith("ExecStart="))
        script = exec_line.split()[-1]
        assert (ROOT / script).is_file(), (unit_name, script)


import json
import sys
import pytest
from weather_alpha.ops.health import StrategyHealth


def test_installer_renders_actual_selected_runtime(tmp_path):
    runtime='/opt/custom-runtime/bin/python'
    proc=subprocess.run(['bash',str(ROOT/'scripts/install_contrail_current_timer.sh'),'--dry-run'],
        env={'PATH':'/usr/bin:/bin','ROOT':'/opt/weather-alpha-strategy-release','SOURCE_ROOT':str(ROOT),'PYTHON':runtime},
        capture_output=True,text=True,check=True)
    assert proc.stdout.count('ExecStart='+runtime+' ')==3
    assert '/opt/weather-alpha-signal-pipeline/.venv' not in proc.stdout


def test_installer_rejects_unsafe_runtime_path():
    proc=subprocess.run(['bash',str(ROOT/'scripts/install_contrail_current_timer.sh'),'--dry-run'],
        env={'PATH':'/usr/bin:/bin','ROOT':'/opt/weather-alpha-strategy-release','SOURCE_ROOT':str(ROOT),'PYTHON':'relative python'},
        capture_output=True,text=True)
    assert proc.returncode==2 and 'systemd-safe' in proc.stderr


def test_new_units_and_stack_default_to_independent_runtime():
    for name in ['current','history','q90']:
        text=(ROOT/f'deploy/systemd/weather-contrails-{name}.service').read_text()
        assert 'ExecStart=/opt/weather-alpha-runtime/.venv/bin/python ' in text
    assert '/opt/weather-alpha-runtime/.venv/bin/python' in (ROOT/'scripts/start_paper_strategy_stack.sh').read_text()


@pytest.mark.parametrize('module_name,reason', [
    ('scripts.record_contrail_current','CONTRAIL_SCAN_TIMEOUT'),
    ('scripts.refresh_contrail_hourly_history','HISTORY_REFRESH_TIMEOUT'),
])
def test_contrail_oneshot_timeout_overwrites_old_ok(tmp_path,monkeypatch,module_name,reason):
    import importlib
    module=importlib.import_module(module_name)
    h=StrategyHealth('test',status_dir=tmp_path/'status',events_path=tmp_path/'events')
    h.report('OK','OLD_OK')
    monkeypatch.setattr(module,'StrategyHealth',lambda *a,**kw:h)
    monkeypatch.setenv('GOOGLE_CONTRAILS_API_KEY','test-placeholder')
    monkeypatch.setattr(sys,'argv',['test'])
    def timeout(*a,**kw): raise subprocess.TimeoutExpired('test',kw['timeout'])
    monkeypatch.setattr(module.subprocess,'run',timeout)
    assert module.main()==124
    row=json.loads(h.status_path.read_text())
    assert row['status']=='ERROR' and row['reason']==reason


def test_contrail_q90_timeout_reports_explicit_error_without_trigger(tmp_path,monkeypatch):
    import scripts.run_contrail_q90_monitor as m
    h=StrategyHealth('test',status_dir=tmp_path/'status',events_path=tmp_path/'events');h.report('OK','OLD_OK')
    def timeout(*a): raise subprocess.TimeoutExpired('test',180)
    monkeypatch.setattr(m,'build_panel',timeout)
    m.cycle(ROOT,object(),{},h)
    assert json.loads(h.status_path.read_text())['reason']=='CONTRAIL_PANEL_TIMEOUT'


def test_subprocess_spawn_failure_reports_health(tmp_path):
    from weather_alpha.ops.health import run_health_subprocess
    h=StrategyHealth('test',status_dir=tmp_path/'status',events_path=tmp_path/'events')
    assert run_health_subprocess(['/nonexistent/weather-command'],h,'TEST',timeout=1) is None
    assert json.loads(h.status_path.read_text())['reason']=='TEST_SPAWN_FAILED'
