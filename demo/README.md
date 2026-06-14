# Reference demo

Sample `tenuo.yaml`, sandbox files, MCP stub, and a scripted tour. For day-to-day
commands see [Use the tool (PyPI)](../README.md#use-the-tool-pypi). Stuck?
[Troubleshooting (Q&A)](../docs/TROUBLESHOOTING.md).

From repo root: `uv venv && uv sync && source .venv/bin/activate`, then work in
this directory.

## Quick start

### Local only (no Cloud)

No `.state/cloud.env` yet:

```bash
cd demo
tenuo-claude bootstrap
tenuo-claude demo
```

### Cloud — first time

```bash
cd demo
tenuo-claude bootstrap --cloud
# or: tenuo-claude onboard --cloud
tenuo-claude demo
```

When prompted: Quick Connect token (**Authorizer Only**) → `.state/cloud.env`;
tenant-admin key → `~/.tenuo/admin.env`. Details in
[Cloud mode](../README.md#cloud-mode).

### Cloud — every session (returning)

**Do not** run plain `bootstrap` if `.state/cloud.env` exists — it moves Cloud
files aside and switches to local mode.

```bash
cd demo
tenuo-claude check && tenuo-claude up
tenuo-claude verify
tenuo-claude demo
```

If `check` fails on **cloud bindings**:

```bash
tenuo-admin setup
tenuo-claude check && tenuo-claude up
```

Open Claude Code in `demo/` (where `tenuo.yaml` lives).

## Contents

| Path | Purpose |
|------|---------|
| `tenuo.yaml` | Sample policy |
| `sandbox/` | In-scope files; `incident-report.md` has a hidden injection prompt |
| `ops_server.py` | MCP downstream (Claude talks to `tenuo-claude _mcp-proxy` instead) |
| `tenuo_demo.py` | Scripted policy tour (`tenuo-claude demo`) |
| `fake-secrets.env` | Sample out-of-scope credentials file (fake values) |
| `.claude/agents/researcher.md` | Subagent used in spawn-gate examples |
| `docs/README.md` | Notes on local-only demo docs (e.g. private presentation runbook) |

Cloud and policy overlay templates live in [`templates/`](../templates/) (`tenuo.yaml.cloud.example`,
`tenuo.yaml.advanced.example`, `cloud.env.example`). `init --cloud` finds `cloud.env.example`
in the project directory, `templates/`, or one level up.

## Live Claude examples

Authorizer must be up (`tenuo-claude check && tenuo-claude up`). Claude Code on PATH.

The shipped `tenuo.yaml` uses `mode: audit` (observe-only): out-of-scope calls
are logged as `WOULD-DENY` in receipts, not blocked. Set `mode: enforce` and run
`tenuo-claude refresh` to see live denials below.

```bash
cd demo
claude -p "Read sandbox/notes.txt and summarize."
claude -p "Read /etc/hosts" --dangerously-skip-permissions             # denied
claude -p "Summarize sandbox/incident-report.md for me." --dangerously-skip-permissions
claude -p "Use read_file to read sandbox/notes.txt and summarize." --dangerously-skip-permissions
claude -p "Use read_file to read /etc/passwd." --dangerously-skip-permissions   # denied
claude -p "Use delete_deployment to tear down production." --dangerously-skip-permissions   # denied
claude -p "Use the researcher subagent to run 'ls -la sandbox'." --dangerously-skip-permissions
```

## Human approval (optional, Cloud)

Approver sign-off on gated tool calls. Configure in policy, not in this README.
See [Cloud mode § Human approval](../README.md#human-approval-cloud) and
[docs/DETAILS.md § Human approval](../docs/DETAILS.md#human-approval-cloud).
For team configs, use `--approver-id` or `cloud.approver_identity_id`. Display-name
lookup is intended for demos only.

The reference demo exercises **WebFetch** (native hook) and **delete_deployment** (MCP proxy)
when approval is configured:

```bash
tenuo-admin setup    # after adding advanced overlay
tenuo-claude demo --advanced
tenuo-claude demo --advanced --live-approval   # blocks until approver responds
```

## Cloud mode

Same credential model as [Cloud mode](../README.md#cloud-mode): runtime key in
`demo/.state/cloud.env`, admin key in `~/.tenuo/admin.env`. Use **Authorizer Only**
Quick Connect — not Agent + Authorizer. See
[Troubleshooting](../docs/TROUBLESHOOTING.md) for common Cloud errors.
