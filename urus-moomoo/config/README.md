# Local-only collection task configuration

Create `collection_tasks.local.toml` on the machine that runs the collector.
It is intentionally not supplied here: task definitions are deployment-owned
operational data and must not be uploaded to GitHub.

The loader expects a `[runtime]` table with `timezone` and `output_dir`, plus
one or more `[[tasks]]` tables containing `name`, `enabled`, `schedule`,
`symbols`, and an allowed `operation`. A task may also define
`delay_seconds` and a private `[tasks.params]` table. Provider-specific option
settings belong in the private `[runtime.options]` table. Keep all actual values only in the local file. Keep this file free
of credentials; use the host's environment or secret store for OpenD
connection details.

To publish complete post-close evidence into the main Urus API, configure a
private `[runtime.publisher]` table with `api_url`, `timeout_seconds`, and an
optional `state_path`. Publication waits for exact, complete `instrument_cards`
and `options_snapshot` captures for the same market trading date and sends their
immutable IDs and SHA-256 digests to `/api/observation/runs`. The intraday/EOD
`capture_eod_options` task is deliberately not a substitute for
`options_snapshot`.
