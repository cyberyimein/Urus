# Local-only collection task configuration

Create `collection_tasks.local.toml` on the machine that runs the collector.
It is intentionally not supplied here: task definitions are deployment-owned
operational data and must not be uploaded to GitHub.

The loader accepts a TOML document with:

```toml
[runtime]
timezone = "America/New_York"
output_dir = "data"

[[tasks]]
name = "local-private-task-name"
enabled = true
schedule = "local-private-schedule"
symbols = ["local-private-symbols"]
```

Replace every example value locally before starting the collector. Keep this
file free of credentials; use the host's environment or secret store for
OpenD connection details.
