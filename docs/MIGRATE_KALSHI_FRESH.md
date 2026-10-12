# Kalshi fresh-data handover

Approved source release: 9fdf49a0797719e7ca4fccbae3cb12b3f94398b9. Independent Python: /opt/weather-alpha-runtime/.venv/bin/python. Both the collector and catalog service units pin this immutable release. Fresh destination: /data/weather/rolling30/kalshi. No historical archive, catalog or ticker cache is imported; build catalog directly from Kalshi before cutover.

Prepare: verify source blob hashes against GitHub, run tests/test_kalshi_l2_archive.py and tests/test_live_kalshi_ws.py, render and systemd-analyze verify both units. Back up installed units and status paths under /opt/weather-alpha-runtime/migrations/kalshi-fresh-9fdf49a.

Cutover: stop catalog timer, gracefully stop old collector, install both units, daemon-reload, start collector and resume catalog timer. Preserve unrelated collector, snapshot and Telegram processes. After acceptance, canonical catalog/health/BBO status paths may point to fresh outputs. Existing status files are retained in migration backup for rollback.

Acceptance: current catalog membership and active series count match health; all subscribed contracts receive orderbook snapshots; health and last-message timestamps age at most10s; BBO generated timestamp age at most15s with nonzero temperature quotes and at least one valid fresh quote. Confirm0 sequence gaps and duplicates/reorders, no error, new raw gzip grows, and one collector process. Observe message growth across at least60s. Record per-quote freshness rather than treating a freshly-written file as proof every inactive quote is current. Prior PR19 isolated forced-close/gap recovery tests remain the reconnect evidence; a service restart is a separate recovery check.

Rollback: stop new collector and catalog timer; restore backed-up unit files and any changed status paths; daemon-reload; start original collector and timer. Keep old dataset until accepted and a separate deletion step verifies no references. On failed acceptance, rollback automatically and retain fresh test output for diagnosis.

30-day Kalshi raw retention is pending separately. Never reuse the Polymarket-only retention script against Kalshi. Results, timestamps, deployed configuration commit and limitations are recorded in issue #22.
