# Demo docs (local)

Public install and Cloud guides live in the [product README](../../README.md) and
[docs/TROUBLESHOOTING.md](../../docs/TROUBLESHOOTING.md).

**`PRESENTATION.md`** in this folder is **gitignored** — a private runbook for live
demos (talk track, beats, day-of checklist). Copy or create it locally; it is not
published on GitHub.

If you maintain a local `PRESENTATION.md`, keep its prep commands aligned with:

```bash
# First-time Cloud (from demo/)
tenuo-claude bootstrap --cloud
# or: tenuo-claude onboard --cloud

# Every session
tenuo-claude check && tenuo-claude up && tenuo-claude verify
```

See [TROUBLESHOOTING.md](../../docs/TROUBLESHOOTING.md) when prep fails.
