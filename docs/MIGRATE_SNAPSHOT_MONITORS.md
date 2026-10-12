# Fresh snapshot and monitor migration

Three units: weather-snapshot-producer, weather-structural-alpha, weather-telegram. All use /opt/weather-alpha-monitor-current pointing to the recorded immutable GitHub release, independent runtime, fresh /data/weather/rolling30/snapshots and monitors destinations.

Snapshot producer retains existing reviewed read-only dashboard source and provisional model labels. It fetches REST contracts and surface observations; --current-only replaces current JSONL streams atomically, including clearing empty contract snapshots. No old paper ledger or L2 snapshot is imported. Dashboard L2 enrichment is unavailable; structural monitor independently consumes validated fresh Kalshi BBO. This migration does not claim a new probability model or new settlement validation.

Explicit health output options isolate producer/structural status and events from old live data. Structural stale-quote, settlement and ladder gates remain intact; DEGRADED/abstention may be correct. Telegram consumes fresh alpha/health and absent fresh inactive-experiment streams. No contrail history imported; historical commands remain unavailable until fresh contrail history is populated. Only the existing Telegram update cursor may carry over as operational control state to avoid replaying commands; no signals/fills/seen ledger imported. No Telegram test message.

Prepare full release from GitHub, verify blobs, run tests, verify units and independent imports. Record old commands/PIDs for rollback; stop one named tmux job at a time, install/start corresponding unit, require new output/health advancement before moving on. Validate at least two producer generations, monitor heartbeat and Telegram API authentication/readiness, with collectors unchanged. Enable each accepted service.

Rollback a failed handover: stop/disable affected new unit, relaunch the recorded old tmux command while old data/worktree still exists. After old data or source removal, this rollback is retired; use published immutable code and fresh paths for any subsequent rollback.

Old live data and code worktrees may be removed only after active/scheduled jobs, proc descriptors/cwd, status symlinks, Python and shared Git metadata dependencies are verified. Debug REPLs must be retired before deleting their cwd or runtime. Separate projects, credentials, OS agents and archived GitHub evidence are outside removal. Results in issue22.
