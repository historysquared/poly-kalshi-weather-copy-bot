from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import pytest
from scripts.expire_polymarket_us_30d import expire

NOW = datetime(2026,10,12,3,tzinfo=timezone.utc)
def archive(root, day, name=None):
    d = root / day.replace("-", "/"); d.mkdir(parents=True, exist_ok=True)
    p = d / (name or f"polymarket_us_weather_{day}T00.jsonl.gz")
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
    elif fault=="wrong_date": p.rename(p.parent/"polymarket_us_weather_2026-09-02T00.jsonl.gz")
    else: os.utime(p,None)
    result=expire(tmp_path,now=NOW,execute=True)
    assert result["blocked"] and result["deleted_files"]==0

def test_refuses_symlink_root(tmp_path):
    real=tmp_path/"real";real.mkdir()
    link=tmp_path/"link";link.symlink_to(real,target_is_directory=True)
    with pytest.raises(ValueError):expire(link,now=NOW,execute=True)

def test_empty_archive_is_safe(tmp_path):
    assert expire(tmp_path,now=NOW,execute=True)["deleted_files"]==0
