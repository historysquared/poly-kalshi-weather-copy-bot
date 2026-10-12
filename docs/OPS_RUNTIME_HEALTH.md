# Runtime and health contract

New contrail service templates and the paper-stack launcher default to `/opt/weather-alpha-runtime/.venv/bin/python`, outside Git worktrees. The contrail installer validates an absolute systemd-safe PYTHON override and renders that exact interpreter into all three installed service ExecStart lines. Dry-run displays the rendered lines. This source change does not create the venv or migrate existing services; release promotion and runtime provisioning are separate deployment steps.

Health freshness is evaluated at read time, including when a file stops changing. Default strategy TTL is 180 seconds; current contrail snapshot and q90 monitor TTL is 1200 seconds; daily history TTL is 27 hours. Kalshi collector status requires both generated_at and last_message_at within 30 seconds. Missing, malformed, timezone-less, future, invalid TTL, or expired health never displays OK.

The Telegram watcher checks freshness each poll, records one error transition and one recovery, retries failed sends, and persists observed component signatures so a disappearing known health file remains monitored. `/status` uses the same freshness logic. This is in-process monitoring: if the watcher or host dies, it cannot send alerts. External dead-man monitoring and supervision remain separate work. Components never observed are not inferred from arbitrary files; expected service inventory is part of the pending deployment checks.

Contrail snapshot/history subprocess timeouts write explicit ERROR health and return 124. Spawn failures also write explicit ERROR. The q90 panel timeout writes CONTRAIL_PANEL_TIMEOUT without emitting a trigger. A subprocess timeout must replace old OK state rather than merely raise and leave it looking healthy.

Existing production remains on its previous runtime/code until an explicit, independently verified deployment. No service install/restart or Telegram delivery is required by the regression tests.
