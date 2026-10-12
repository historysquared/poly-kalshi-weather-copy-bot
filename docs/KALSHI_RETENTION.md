# Kalshi fresh raw retention

Only /data/weather/rolling30/kalshi/raw is processed. The CLI has no root override and defaults to dry run; --execute enables expiry. Keep all UTC partitions whose day ends after now minus30days, so partial-day granularity conservatively keeps up to31days. Never touch legacy archives or Polymarket data.

Expire only matching kalshi_weather_l2_YYYY-MM-DDTHH.jsonl.gz files in matching YYYY/MM/DD directories. Reject symlink root/ancestors/files, invalid dates/names, recently modified old partitions, or changed file fingerprints. Unexpected entries block that whole day. A nonblocking flock serializes runs; blocked paths produce nonzero exit status and retention-status.json. No old-data import, archive or compaction requirement.

Install the standalone script from the recorded GitHub commit under /opt/weather-alpha-runtime/retention-releases/<sha>/; make its source read-only and point /opt/weather-alpha-kalshi-retention-current there. Install and verify the two units. Run dry-run then execute once; inspect status. Timer daily01:10UTC with up to120s jitter and persistent catchup. Rollback: disable/stop weather-retention-kalshi.timer and service; collectors remain running. Deleted expired data is not recoverable by disabling the timer.

Tests cover30-day boundary/current partition, dry-run, wrong venue/name/date, recent modification, symlinks and empty archive. Operational results and legacy dependency checks are in issue22. Old archive removal remains separate from ongoing fresh retention.
