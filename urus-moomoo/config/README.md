# Local-only collection task configuration

Create `collection_tasks.local.toml` on the machine that runs the collector.
It is intentionally not supplied here: task definitions are deployment-owned
operational data and must not be uploaded to GitHub.

The loader expects a `[runtime]` table with `timezone` and `output_dir`, plus
one or more `[[tasks]]` tables containing `name`, `enabled`, `schedule`, and
`symbols`. Keep the actual values only in the local file. Keep this file free
of credentials; use the host's environment or secret store for OpenD
connection details.
