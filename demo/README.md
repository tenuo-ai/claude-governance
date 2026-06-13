# Reference demo

Sample `tenuo.yaml`, sandbox files, MCP stub, and a scripted tour. For day-to-day
setup, see [Use the tool (PyPI)](../README.md#use-the-tool-pypi). This folder is
the reference demo when working from a git checkout.

## Quick start

From the repo root (after `uv sync` or `pip install tenuo-claude-code`):

**Local, first run** (no Cloud credentials yet):

```bash
cd demo
tenuo-claude bootstrap
tenuo-claude demo
```

**Cloud already wired** (`.state/cloud.env` present): skip `bootstrap`. Plain `bootstrap` is local-only and moves Cloud files aside.

```bash
cd demo
tenuo-claude up
tenuo-claude verify
tenuo-claude demo
```

Open Claude Code in `demo/` (where `tenuo.yaml` lives).

## Contents

| Path | Purpose |
|------|---------|
| `tenuo.yaml` | Sample policy |
| `sandbox/` | In-scope files; `incident-report.md` has a hidden injection prompt |
| `ops_server.py` | MCP downstream (Claude talks to `tenuo-claude _mcp-proxy` instead) |
| `tenuo_demo.py` | Tour without Claude; `tenuo-claude demo` wraps this |
| `fake-secrets.env` | Sample out-of-scope credentials file (fake values) |
| `.claude/agents/researcher.md` | Subagent used in spawn-gate examples |

Cloud and policy overlay templates live in [`templates/`](../templates/) (`tenuo.yaml.cloud.example`,
`tenuo.yaml.advanced.example`, `cloud.env.example`). `init --cloud` finds `cloud.env.example`
in the project directory, `templates/`, or one level up.

## Live Claude examples

Authorizer must be up (`tenuo-claude up`). Claude Code on PATH.

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

Without Claude: `tenuo-claude demo`, then `tenuo-claude audit`.

## Human approval (optional, Cloud)

Approver sign-off on gated tool calls. Configure in policy, not in this README.
See [Cloud mode § Human approval](../README.md#human-approval-cloud) and
[docs/DETAILS.md § Human approval](../docs/DETAILS.md#human-approval-cloud).
For team configs, prefer `--approver-id` / `cloud.approver_identity_id`; display
names are kept for quick demos.

The reference demo exercises **WebFetch** (native hook) and **delete_deployment** (MCP proxy)
when approval is configured:

```bash
tenuo-claude demo --advanced
tenuo-claude demo --advanced --live-approval   # blocks until approver responds
```

## Cloud mode

Same flow as [Cloud mode](../README.md#cloud-mode) in the product README, run
from this directory. Quick Connect credentials go in `demo/.state/cloud.env`.
