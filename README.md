# Tenuo for Claude Code

[Tenuo](https://tenuo.ai) governance for [Claude Code](https://code.claude.com/docs):
every agent tool call is checked against a signed warrant (hook → authorizer),
with a receipt on each decision, including under `--dangerously-skip-permissions`.
Policy is `tenuo.yaml`; `init` generates the warrant, authorizer config, Claude
hooks, and MCP proxy wiring.

## Quickstart

Requires Python ≥ 3.10, Docker, and (for live demos) [Claude Code](https://code.claude.com/docs).
Cloud is optional; local mode works with a local issuer key.

**Python environment (recommended — [uv](https://docs.astral.sh/uv/)):**

```bash
uv venv && uv sync
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python3 tenuo_claude.py init   # hooks pin this interpreter — re-run init if you change venvs
python3 tenuo_claude.py up
python3 tenuo_claude.py doctor          # --no-live skips the Claude harness check
python3 tenuo_demo.py                   # allow/deny tour without Claude
```

Or without uv: `python3 -m pip install -r requirements.txt` (same pins).

For root-signed warrants and optional human approval on off-allowlist `WebFetch`,
see [Tenuo Cloud](#what-the-security-team-sees). Reviewer brief:
[docs/SECURITY-TEAM.md](docs/SECURITY-TEAM.md).

## See it in action

These examples run real Claude Code against the policy. `--dangerously-skip-permissions`
turns off Claude's permission prompts; the warrant still applies because enforcement
is in the hook, not in Claude.

In-scope vs out-of-scope reads:

```bash
claude -p "Read sandbox/notes.txt and summarize."                      # allowed
claude -p "Read /etc/hosts" --dangerously-skip-permissions             # denied
```

Destructive instruction with guardrails off:

```bash
claude -p "Use delete_deployment to tear down production." --dangerously-skip-permissions
```

Prompt injection: `sandbox/incident-report.md` hides instructions to exfil secrets and
delete prod. If the model refuses, fine — the warrant still does not grant those tools.

```bash
claude -p "Summarize sandbox/incident-report.md for me." --dangerously-skip-permissions
```

Subagent attenuation (session allows `Bash`; researcher child warrant does not):

```bash
claude -p "Use the researcher subagent to run 'ls -la sandbox' and report the result." \
  --dangerously-skip-permissions
```

Without Claude:

```bash
python3 tenuo_demo.py
python3 tenuo_claude.py audit
```

### Receipt trail

Same demo sequence, real `audit` output (local convenience log; authorizer produces
the signed receipts, streamed to Cloud when connected):

```
$ python3 tenuo_demo.py && python3 tenuo_claude.py audit
  ALLOW      [gov] Read           -> read_file  authorized
  DENY       [gov] Read           -> read_file  Constraint not satisfied
  DENY       [aud] delete_deployment -> unlisted  Constraint not satisfied
  ALLOW      [gov] Bash           -> run_command  authorized
  DENY       [gov] Bash           -> run_command  Constraint not satisfied
  ALLOW      [gov] Grep           -> grep  authorized
  ALLOW      [gov] WebFetch       -> web_fetch  authorized
  DENY       [gov] WebFetch       -> web_fetch  Constraint not satisfied
  ALLOW      [gov] Agent          -> spawn_agent  authorized
  DENY       [gov] Bash           <researcher> -> run_command  Constraint not satisfied
```

With Cloud `WebFetch.approval` enabled, an off-allowlist SSRF-safe URL shows
`PENDING [appr]` before resolve. See [DETAILS.md](docs/DETAILS.md#human-approval-cloud).

## vs. native Claude Code permissions

Claude Code permissions are **configuration**: allow/ask/deny rules in `settings.json`,
optionally locked down fleet-wide via **managed settings**. Tenuo adds a **credential**:
a signed warrant checked on every tool call, with TTL, revocation, and a receipt stream.

Tenuo is built **on top of** Claude's hook and managed-settings mechanisms — not a
replacement. You still deploy PreToolUse hooks (this demo wires them from `tenuo.yaml`);
for fleet enforce, use managed settings so users cannot remove them.

| | Claude Code permissions | Tenuo warrant |
|---|-------------------------|---------------|
| Policy form | Allow/ask/deny rules in settings | Signed credential; Cloud mode chains to tenant root |
| Expiry | Rules persist until edited | Session TTL (~1h); `up` refreshes |
| Revocation | Edit rules; sessions may keep prior allowances | Revoke warrant id; live in ~30s (Cloud), no restart |
| Evidence | Hook logs optional; no signed trail by default | Signed receipt per decision; central stream with Cloud |
| Delegation | Subagents follow project/user tool policy | Cryptographic attenuation; session is the ceiling |
| Exceptions | Additional allow rules | Optional Cloud approval gate on off-allowlist `WebFetch` |
| `--dangerously-skip-permissions` | Bypasses Claude permission prompts* | Warrant still enforced |

\*Managed settings can disable bypass (`disableBypassPermissionsMode`). Verify native
behavior against [Claude Code permissions](https://code.claude.com/docs/en/permissions).

## How it works

![Tenuo + Claude Code — every tool call is checked against policy before it runs](tenuo_claude_code_architecture.svg)

```
                         tenuo.yaml
                   (policy — single source of truth)
                               │
                    init / up generates
                               ▼
         ┌─────────────────────────────────────────────┐
         │  warrant · authorizer config · Claude hooks │
         │              · MCP proxy wiring               │
         └─────────────────────────────────────────────┘
                               │
              ┌────────────────┴────────────────┐
        native tools                      MCP tools
              │                                 │
      PreToolUse hook                    MCP proxy (.mcp.json)
              └────────────┬────────────────┘
                           ▼
                  tenuo_claude.py → authorizer → allow / deny → receipt
```

On each tool call the hook or MCP proxy signs a proof-of-possession and asks the
authorizer. The decision lives outside Claude.

| Path | Enforcement |
|------|-------------|
| MCP proxy | Structural: Claude talks to the proxy, not the downstream server |
| PreToolUse hook | Cooperative: returns allow/deny; hardened via fail-closed + managed settings |

Both use the same warrant and authorizer. See [DETAILS.md](docs/DETAILS.md#why-hook-and-mcp-proxy).

## What the security team sees

With [Tenuo Cloud](https://cloud.tenuo.ai), each session warrant chains to your tenant
root. Platform security gets one stream to answer: *what did agents do, under what
authority, who approved the exceptions* — and can revoke a compromised warrant in
about 30 seconds without touching the laptop.

Admin vs runtime separation:

| Tool | Key | Does |
|------|-----|------|
| `tenuo_admin.py setup` | admin (`~/.tenuo/admin.env`) | Register holder, create trigger from `tenuo.yaml` |
| `tenuo_claude.py up` | authorizer (`.state/cloud.env`) | Fire trigger, run authorizer |

Runtime refuses to start if an admin key is in the environment.

**Cloud credentials** — two keys, two files:

| Key | Where you get it | File |
|-----|------------------|------|
| **Authorizer** | Quick Connect or dashboard API key (authorizer scope) | `.state/cloud.env` |
| **Admin** | **Not** in Quick Connect — create in dashboard (Admin scope) or use the key from tenant onboarding | `~/.tenuo/admin.env` |

```bash
cp cloud.env.example .state/cloud.env      # authorizer key + API URL
cp admin.env.example ~/.tenuo/admin.env    # admin key (setup only)
# merge tenuo.yaml.cloud.example into tenuo.yaml, then:
python3 tenuo_admin.py setup               # once (needs both files)
python3 tenuo_claude.py init && python3 tenuo_claude.py up
```

See `tenuo.yaml.cloud.example` for the Cloud policy overlay. Full presentation
runbook: [docs/PRESENTATION.md](docs/PRESENTATION.md).

### Cloud audit stream

Every hook and demo decision is also a **signed receipt** in [cloud.tenuo.ai](https://cloud.tenuo.ai):
allow, deny, spawn, and (when configured) human-approved exceptions — one stream for
the whole fleet.

![Authorization receipts in Tenuo Cloud](docs/images/cloud-audit-stream.png)

Drill into an approved off-allowlist `WebFetch` to see the approval bound to that
specific call — approver, timestamp, and cryptographic request hash:

![Receipt detail with human approval](docs/images/cloud-receipt-approval-detail.png)

**Revocation:** revoke the session warrant id from `tenuo-claude status` or the Cloud
dashboard; authorizers pick up the SRL within ~30s. Local-only mode:
`tenuo-claude revoke`.

One-page brief for security reviewers: [docs/SECURITY-TEAM.md](docs/SECURITY-TEAM.md).

## Policy (`tenuo.yaml`)

Warrant, routes, hooks, and MCP wiring come from one file:

```yaml
name: claude-code-demo
sandbox: ./sandbox
mode: enforce
enforce:
  Read:  "subpath:{sandbox}"
  Bash:  "shlex:ls,pwd,echo,date"
  WebFetch:
    domains: ["api.github.com", "*.githubusercontent.com", "*.tenuo.ai"]
default: deny
subagents:
  researcher:
    tools: [Read, Grep, Glob]
mcp:
  downstream: ./ops_server.py
  enforce:
    read_file: "subpath:{sandbox}"
```

- `enforce`: allowed and argument-checked.
- `audit`: harness tools from `harness_tools.yaml` (extend with `audit_extra:`).
- `default: deny`: everything else blocked with a receipt.
- `mcp.enforce`: bare downstream tool name + `path` arg only (single MCP server in this demo).
- With `subagents:` on, bundled **Workflow** is audit-allowed but its inner agent
  calls use undeclared roles and are denied — see [DETAILS.md](docs/DETAILS.md#subagents).

## Commands

| Command | Does |
|---------|------|
| `init` | Mint warrant, wire hooks and `.mcp.json` |
| `up` / `down` | Start / stop authorizer |
| `status` | Warrant, posture, Cloud summary |
| `doctor [--no-live]` | Self-test allow/deny |
| `audit [--tail N]` | Receipt trail |
| `revoke` | Revoke session warrant |

## Enterprise deployment

Install the hook via **managed settings** (above user/project settings):

```jsonc
// macOS: /Library/Application Support/ClaudeCode/managed-settings.json
{
  "hooks": {
    "PreToolUse":  [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 /opt/tenuo/tenuo_claude.py _hook"}]}],
    "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 /opt/tenuo/tenuo_claude.py _post"}]}]
  }
}
```

Deploy with MDM alongside the CLI and `tenuo.yaml`. Governance covers agent tool
calls, not interactive `!` shell — restrict that at the workstation if needed.

## Rolling out

1. **Local eval** — this repo: `init`, `up`, `doctor`, `tenuo_demo.py`.
2. **Observe-only** — `mode: audit`: compute and receipt real allow/deny without
   blocking. The hook emits **no** permission decision, so observe-only never weakens
   Claude's stock prompts. Tune on `WOULD-DENY` rows, then set `mode: enforce`.
3. **Fleet enforce** — managed settings + Cloud root-signed warrants + team policy in
   `tenuo.yaml`.

Send security reviewers [docs/SECURITY-TEAM.md](docs/SECURITY-TEAM.md). Mechanics:
[docs/DETAILS.md](docs/DETAILS.md).

## Security boundaries

Tenuo controls which tool calls the agent may make, not every execution side effect.
[The Map is not the Territory](https://niyikiza.com/posts/map-territory/).

Claude Code only blocks PreToolUse on exit code 2 or explicit deny; `_hook` converts
errors into deny decisions. `doctor --no-live` skips the live Claude harness check.

**Fail-closed** (run live for prospects):

```bash
mv tenuo.yaml tenuo.yaml.bak
# every tool call denied: Tenuo hook error (fail-closed): Missing …/tenuo.yaml
mv tenuo.yaml.bak tenuo.yaml
```

Limits: Bash allowlist checks command shape; WebFetch checks URL strings; new Claude
tools default-deny until listed in `harness_tools.yaml`.

**Claude Code version assumptions:** spawn routing keys on tool names `Agent` /
`Task` and the `agent_type` hook field (empirically claude 2.1.x). `doctor --live`
checks PreToolUse exit-code semantics (exit 2 blocks). If Anthropic renames spawn
tools, spawns fail closed unless the new name is only audit-listed.

## Files

| File | Purpose |
|------|---------|
| `tenuo.yaml` | Policy |
| `harness_tools.yaml` | Bundled harness tool allowlist |
| `docs/SECURITY-TEAM.md` | One-page reviewer brief |
| `docs/DETAILS.md` | Deep dive (SSRF examples, audit invariants, subagents) |
| `docs/images/` | Cloud audit stream + approval receipt screenshots |
| `tenuo_claude.py` | CLI, hook, MCP proxy |
| `tenuo_demo.py` | Scripted tour + receipt trail |
| `CONTRIBUTING.md` | Maintainer notes |

Maintainer setup: [CONTRIBUTING.md](CONTRIBUTING.md).
