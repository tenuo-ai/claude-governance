# Reference demo

Sample `tenuo.yaml`, sandbox files, MCP stub, and a scripted tour. For day-to-day
use on a governed project, follow [Use the tool (PyPI)](../README.md#use-the-tool-pypi)
in the main README. You do not need this folder.

## Quick start

From the repo root (after `uv sync` or `pip install tenuo-claude-code`):

```bash
cd demo
tenuo-claude bootstrap --local    # check → init → up → verify
tenuo-claude demo                 # scripted allow/deny tour
```

Open Claude Code in `demo/` (where `tenuo.yaml` lives).

## Contents

| Path | Purpose |
|------|---------|
| `tenuo.yaml` | Sample policy |
| `sandbox/` | In-scope files; `incident-report.md` has a hidden injection prompt |
| `ops_server.py` | MCP downstream (Claude talks to `tenuo-claude _mcp-proxy` instead) |
| `tenuo_demo.py` | Tour without Claude; `tenuo-claude demo` wraps this |
| `prod-credentials.env` | Sample out-of-scope credentials file (fake values) |
| `.claude/agents/researcher.md` | Subagent used in spawn-gate examples |

Cloud and policy overlay templates (`tenuo.yaml.cloud.example`,
`tenuo.yaml.advanced.example`, `cloud.env.example`) are at the repo root.

## Live Claude examples

Authorizer must be up (`tenuo-claude up`). Claude Code on PATH.

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

The reference demo exercises the **WebFetch** example when approval is configured:

```bash
tenuo-claude demo --advanced
tenuo-claude demo --advanced --live-approval   # blocks until approver responds
```

## Cloud mode

Same flow as [Cloud mode](../README.md#cloud-mode) in the product README, run
from this directory. Quick Connect credentials go in `demo/.state/cloud.env`.
