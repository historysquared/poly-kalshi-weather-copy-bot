from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import pytest
from scripts.expire_kalshi_30d import expire

NOW = datetime(2026,10,12,3,tzinfo=timezone.utc)
def archive(root, day, name=None):
    d = root / day.replace("-", "/"); d.mkdir(parents=True, exist_ok=True)
    p = d / (name or f"kalshi_weather_l2_{day}T00.jsonl.gz")
    p.write_bytes(b"raw")
    old = (NOW-timedelta(days=40)).timestamp()
    os.utime(p, (old,old))
    return p

def test_keeps_current_and_complete_thirty_day_window(tmp_path):
    old=archive(tmp_path,"2026-09-10")
    boundary=archive(tmp_path,"2026-09-11")
    recent=archive(tmp_path,"2026-09-12")
    today=archive(tmp_path,"2026-10-12")
    result=expire(tmp_path,now=NOW,execute=True)
    assert result["deleted_files"]==2
    assert not old.exists() and not boundary.exists()
    assert recent.exists() and today.exists()

def test_dry_run_never_deletes(tmp_path):
    p=archive(tmp_path,"2026-09-01")
    result=expire(tmp_path,now=NOW)
    assert result["eligible_files"]==1 and result["deleted_files"]==0 and p.exists()

@pytest.mark.parametrize("fault",["symlink","unexpected","wrong_date","recent_write"])
def test_suspicious_day_is_preserved(tmp_path,fault):
    p=archive(tmp_path,"2026-09-01")
    if fault=="symlink":
        p.unlink(); p.symlink_to(tmp_path/"outside")
    elif fault=="unexpected": (p.parent/"unrelated").write_text("keep")
    elif fault=="wrong_date": p.rename(p.parent/"kalshi_weather_l2_2026-09-02T00.jsonl.gz")
    else: os.utime(p,None)
    result=expire(tmp_path,now=NOW,execute=True)
    assert result["blocked"] and result["deleted_files"]==0

def test_refuses_symlink_root(tmp_path):
    real=tmp_path/"real";real.mkdir()
    link=tmp_path/"link";link.symlink_to(real,target_is_directory=True)
    with pytest.raises(ValueError):expire(link,now=NOW,execute=True)

def test_empty_archive_is_safe(tmp_path):
    assert expire(tmp_path,now=NOW,execute=True)["deleted_files"]==0

def test_other_venue_archive_blocks_whole_day(tmp_path):
    keep=archive(tmp_path,"2026-09-01")
    foreign=archive(tmp_path,"2026-09-01","polymarket_us_weather_2026-09-01T00.jsonl.gz")
    result=expire(tmp_path,now=NOW,execute=True)
    assert result["blocked"] and keep.exists() and foreign.exists()

def test_symlink_day_preserves_external_archive(tmp_path):
    outside=tmp_path/"outside"; outside.mkdir()
    p=outside/"kalshi_weather_l2_2026-09-01T00.jsonl.gz";p.write_bytes(b"keep")
    parent=tmp_path/"2026"/"09";parent.mkdir(parents=True)
    (parent/"01").symlink_to(outside,target_is_directory=True)
    assert expire(tmp_path,now=NOW,execute=True)["blocked"] and p.exists()

def test_exact_midnight_boundary(tmp_path):
    p=archive(tmp_path,"2026-09-11")
    midnight=datetime(2026,10,12,0,tzinfo=timezone.utc)
    assert expire(tmp_path,now=midnight-timedelta(microseconds=1))["eligible_files"]==0
    assert expire(tmp_path,now=midnight)["eligible_files"]==1 and p.exists()

def test_naive_clock_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        expire(tmp_path,now=NOW.replace(tzinfo=None),execute=True)
