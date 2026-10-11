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
