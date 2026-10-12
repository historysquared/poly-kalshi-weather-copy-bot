from datetime import datetime, timedelta, timezone
import pytest
from scripts.check_polymarket_us_freshness import check, VERSION

NOW = datetime(2026, 10, 12, 2, tzinfo=timezone.utc)

def rows():
    h = dict(generated_at=NOW.isoformat(), collector_version=VERSION, read_only=True,
             event_count=1, market_count=1, last_error=None, consecutive_failures=0)
    c = dict(local_receive_timestamp=NOW.isoformat(), collector_version=VERSION,
             read_only=True, live_order_submission=False, event_count=1, market_count=1, markets=[{}])
    return h, c

def test_successful_matching_cycle_is_fresh():
    assert check(*rows(), now=NOW) == []

@pytest.mark.parametrize("fault", ["stale", "future", "naive", "error", "no_advance", "mismatch", "orders", "wrong_version", "bad_count", "bad_rows"])
def test_handover_rejects_invalid_or_old_success(fault):
    h, c = rows()
    after = None
    if fault == "stale": h["generated_at"] = (NOW-timedelta(seconds=151)).isoformat()
    elif fault == "future": h["generated_at"] = (NOW+timedelta(seconds=1)).isoformat()
    elif fault == "naive": h["generated_at"] = NOW.replace(tzinfo=None).isoformat()
    elif fault == "error": h["last_error"] = "offline"
    elif fault == "no_advance": after = NOW
    elif fault == "mismatch": h["generated_at"] = (NOW-timedelta(seconds=1)).isoformat()
    elif fault == "orders": c["live_order_submission"] = True
    elif fault == "wrong_version": h["collector_version"] = "other"
    elif fault == "bad_count": h["event_count"] = 0
    elif fault == "bad_rows": c["markets"] = []
    assert check(h, c, now=NOW, after=after)
