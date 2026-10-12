# Prepared release; no service migration

Merged GitHub commit: d013a3b1452af5a9cb64c9ee1ede6c3e4c50c44b.
Source tree: 3526576fea510dbeb028c928b6e39bc995c7ec34.
Release: /opt/weather-alpha-releases/d013a3b1452af5a9cb64c9ee1ede6c3e4c50c44b (237 tracked files hash/mode verified, no extras, write permissions removed).
Runtime: /opt/weather-alpha-runtime/.venv, Python 3.12.3, independent of worktree environments; project installed as a non-editable wheel.

Validation: 237 tests, 73 runtime imports, 70 CLI checks, pip check, both deployment dry-runs passed in the new runtime. Requirements and wheel hashes are in the manifest. No service unit, active release symlink, production runtime or live data was migrated/modified. Production collector PID 969321/start 2026-09-30 06:53:09 UTC unchanged.

Dependencies were seeded from the previously validated environment, then resolved against the merged project's declared requirements. s3fs 0.4.2 was below the project minimum and became 2026.9.0; botocore became 1.43.106, with s3fs async dependencies installed. These adjustments affect only the new runtime and were followed by the full checks above. The lock records actual final versions.

For restoration, export the exact merged source, create a Python 3.12 venv, install pinned third-party lines from requirements.runtime.lock (excluding the local weather-alpha-lab wheel URL), build the project wheel from that source, and install it non-editably. Verify source hashes and rerun acceptance. The manifest's file wheel URL refers to the prepared server artifact; it is not a public download URL.

Do not run the stack launcher or installer without a separately reviewed migration. The stable service release aliases have not been created/promoted. Source merge does not deploy. Runtime provisioning is complete, activation is not. Read-only permissions are operational protection, not root-proof immutability.
