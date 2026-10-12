# Fresh Polymarket US retention

Only /data/weather/rolling30/polymarket_us/raw is managed. Keep at least thirty days: a UTC-day partition expires only when its entire day ended at least thirty days before current UTC time. Thus the current partial day plus boundary-day data can exceed thirty calendar partitions slightly. Run daily at 01:00 UTC, persistent timer. No old data import or compaction/archive gate is required by the user's current policy.

Default CLI is dry-run; systemd passes --execute. Reject symlink roots/partitions, unexpected filenames or dates, and recently modified old partitions; recheck file identity before unlink. Lock prevents concurrent runs. Never touch current/health snapshots, legacy archives, Kalshi, or unrelated directories. Store compact last-run status and journal output. Eligible files are absent initially, so activating on the fresh dataset deletes nothing.

Install source from a pinned, write-protected GitHub release and link /opt/weather-alpha-retention-current to it. The independent runtime executes the script. Enable only this timer; no collector restart needed. Disable timer/service to pause retention. Thirty-day policy applies to this fresh collector only until other collectors migrate.
