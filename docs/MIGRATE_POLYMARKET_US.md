# Polymarket US: staged service migration

Status: PREPARED, NOT ACTIVATED. No service was installed, enabled, restarted or migrated.

## Source and paths
Approved collector release: d013a3b1452af5a9cb64c9ee1ede6c3e4c50c44b.
Release tree: 3526576fea510dbeb028c928b6e39bc995c7ec34.
Runtime: /opt/weather-alpha-runtime/.venv.
Staged unit/checker: /opt/weather-alpha-runtime/migrations/polymarket-us-d013a3b/.
Unit: weather-collector-polymarket-us.service.
The candidate deliberately pins the existing approved collector SHA; merging these configuration files does not activate anything.

Observed legacy service:
- tmux session weather-poly-us
- shell PID 816999, collector PID 817001 (revalidate before handover)
- code /opt/weather-alpha-market-universe
- Python /opt/weather-alpha-signal-pipeline/.venv/bin/python
- healthy 13 events / 71 markets during preparation

## Preparation validation
systemd-analyze verify passed.
Freshness checker passed on the legacy live files.
An isolated --once run from the approved release using the new runtime produced 13 events / 71 markets under /data/weather/isolated-tests/polymarket-us-migration-d013a3b/, with no production writes.
The checker requires matching successful health/current cycles, timezone-aware nonfuture timestamps <=150 seconds, correct collector version, positive/matching counts and market-row count, read-only=true, orders disabled, zero failures and no error. --after proves a new successful cycle rather than rereading an old file.

## Handover (future execution only)
1. Confirm main contains the approved migration configuration, the release manifest hashes still match, the new runtime passes pip check, both output directories exist, and only one legacy collector is present. Record fresh Polymarket and Kalshi health, Kalshi systemd PID/start, and the baseline Polymarket generated_at. Refuse if service is already active or a second collector exists.
2. Preserve the rollback paths listed below until this service and every remaining dependent service have migrated.
3. Install the reviewed candidate unit and daemon-reload. Do not start it yet:
   install -m 0644 /opt/weather-alpha-runtime/migrations/polymarket-us-d013a3b/weather-collector-polymarket-us.service /etc/systemd/system/weather-collector-polymarket-us.service
   systemctl daemon-reload
4. Gracefully interrupt ONLY the observed legacy Polymarket pane:
   tmux send-keys -t weather-poly-us C-c
   Verify PID 817001 has exited and no record_polymarket_us_weather.py process remains. Do not use a broad pkill. If it has not exited within 30 seconds, stop the handover and investigate.
5. Start the new service (without boot enabling yet):
   systemctl start weather-collector-polymarket-us.service
6. Within 120 seconds require a successful freshness check with --after set to the saved baseline. Verify systemctl MainPID cmdline points to the new runtime and the pinned approved release; confirm exactly one collector process.
7. Wait for another successful cycle and check --after using the first accepted cycle. Observe for at least five cycles. Verify journal has no repeated exceptions, zero consecutive_failures, current/health timestamps advance, raw archive grows and Kalshi PID/start/gaps/reconnect counts remain unchanged.
8. After acceptance only:
   systemctl enable weather-collector-polymarket-us.service
   Record deployed SHA, runtime lock hash, PID, acceptance timestamps and rollback state in issue #18.

Use the checker from the merged migration source or the staged path, for example:
   /opt/weather-alpha-runtime/.venv/bin/python /opt/weather-alpha-runtime/migrations/polymarket-us-d013a3b/check_polymarket_us_freshness.py --after <saved-time-with-timezone>

A transient current/health mismatch during atomic sequential writes should be retried; it must not be treated as acceptance. Never accept exit code zero from --once alone: the collector can return zero after a failed request, so inspect/check health explicitly.

## Rollback
Trigger rollback if no successful new cycle within 120 seconds, repeated errors/restarts, wrong code/runtime, duplicate collector, or malformed/stale output.
1. Stop and disable ONLY the new service:
   systemctl disable --now weather-collector-polymarket-us.service
2. Confirm its MainPID exited and no collector remains. Its KillSignal=SIGINT/TimeoutStopSec=30 allow the gzip writer to close normally. Do not restart legacy while it still runs.
3. If weather-poly-us no longer exists, restore the captured command:
   tmux new-session -d -s weather-poly-us "cd /opt/weather-alpha-market-universe && PYTHONPATH=. /opt/weather-alpha-signal-pipeline/.venv/bin/python scripts/record_polymarket_us_weather.py >> /data/weather/live/polymarket_us_weather.log 2>&1"
   If the session still exists with a stopped shell, inspect it and reuse it; never blindly create a second writer.
4. Require fresh matching current/health with --after set to rollback start time, positive counts, no errors, one collector, and unchanged Kalshi service.
5. Record rollback and failure reason. No successful deployment claim if rollback was needed.

The old process and new unit use the same canonical output paths, serially. Raw data preservation is not a cleanup requirement chosen by the user, but avoiding concurrent gzip writers and broken shared runtime dependencies is required for a reliable handover.

## Cleanup preference
The user considers DigitalOcean code/data disposable and authorizes removing stale code copies. GitHub is authoritative; no extra archival/backfill gate is imposed on those copies. Delete unused outdated copies after identifying active references. Do not remove /opt/weather-alpha-market-universe or /opt/weather-alpha-signal-pipeline/.venv yet: Kalshi and other active services still use them, and this first migration has not occurred. The separate offline droplet's role remains unidentified; this preparation does not authorize guessing which infrastructure belongs to the weather stack.
