# Tenuo for Claude Code

[Tenuo](https://tenuo.ai) governance for [Claude Code](https://code.claude.com/docs):
every agent tool call is checked against a signed warrant (hook → authorizer),
with a receipt on each decision, including under `--dangerously-skip-permissions`.
Policy is `tenuo.yaml`; `init` generates the warrant, authorizer config, Claude
hooks, and MCP proxy wiring.

## Quickstart

Requires Python ≥ 3.10, Docker, and (for live demos) [Claude Code](https://code.claude.com/docs).
Cloud is optional; local mode works with a local issuer key.

```bash
python3 -m pip install -r requirements.txt
python3 tenuo_claude.py init
python3 tenuo_claude.py up
python3 tenuo_claude.py doctor          # --no-live skips the Claude harness check
python3 tenuo_demo.py                   # allow/deny tour without Claude
```

For root-signed warrants and optional human approval on off-allowlist `WebFetch`,
see [Tenuo Cloud](#tenuo-cloud).

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
claude -p "There's a bad release in prod. Use the delete_deployment MCP tool \
  to tear down the production deployment now." --dangerously-skip-permissions
```

The warrant never granted `delete_deployment`, so the MCP proxy denies it before
the call reaches the server. `"Read prod-credentials.env and print the Stripe key."`
is denied for the same reason: reads are scoped to `./sandbox`.

Prompt injection in data: `sandbox/incident-report.md` includes a hidden instruction
to read `prod-credentials.env`, delete prod, and fetch an off-allowlist URL.

```bash
claude -p "Summarize sandbox/incident-report.md for me." --dangerously-skip-permissions
```

If the model refuses, fine. Either way the warrant does not grant read access to
secrets, prod deletion, or off-allowlist fetch.

Without Claude:

```bash
python3 tenuo_demo.py
python3 tenuo_claude.py audit
```

Subagents cannot widen scope. The demo includes a `researcher` subagent
(`.claude/agents/researcher.md`) whose warrant is the session warrant attenuated
to read/search only:

```bash
claude -p "Use the researcher subagent to run 'ls -la sandbox' and report the result." \
  --dangerously-skip-permissions
```

The researcher warrant does not include `Bash`, even though the session does.
`audit` shows the spawn allowed and the in-subagent `Bash` denied under
`agent_type=researcher`. See [Subagents](#subagents).

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
              │                                 │
        native tools                      MCP tools
  (Read, Bash, WebFetch, …)     (read_file, delete_deployment, …)
              │                                 │
      PreToolUse hook                    MCP proxy (.mcp.json)
              │                                 │
              └────────────┬────────────────────┘
                           ▼
                  tenuo_claude.py
           (authorize each call + write receipts)
                           │
                    HTTP + PoP + warrant
                           ▼
               Tenuo Authorizer (container)
                           │
                     allow / deny
                           ▼
           signed receipt → Tenuo Cloud (optional)
```

On each tool call the hook or MCP proxy signs a proof-of-possession, asks the
authorizer, and enforces the result. The decision lives outside Claude.

### Two paths, one warrant

| Path | Governs | Enforcement |
|------|---------|-------------|
| MCP proxy | Downstream MCP tools | Structural: Claude talks to the proxy, not the server |
| PreToolUse hook | Native tools, MCP names, subagent spawns | Hook returns allow/deny; pair with [managed settings](#enterprise-deployment) and the [fail-closed guard](#security-boundaries) |

Both use the same warrant and authorizer. MCP tools are checked by the proxy and
by the hook on the `mcp__…` name.

## Policy (`tenuo.yaml`)

Warrant, authorizer routes, hooks, and MCP wiring all come from one file:

```yaml
name: claude-code-demo
sandbox: ./sandbox
mode: enforce                           # audit = observe-only (see below)
enforce:
  Read:  "subpath:{sandbox}"
  Write: "subpath:{sandbox}"
  Edit:  "subpath:{sandbox}"
  Bash:  "shlex:ls,pwd,echo,date"
  Glob:  "subpath:{sandbox}"
  Grep:  "subpath:{sandbox}"
  WebFetch:
    domains: ["api.github.com", "*.githubusercontent.com", "*.tenuo.ai"]
    # approval: …  # optional Cloud — tenuo.yaml.cloud.example
default: deny
subagents:
  researcher:
    tools: [Read, Grep, Glob]
mcp:
  downstream: ./ops_server.py
  enforce:
    read_file:      "subpath:{sandbox}"
    list_directory: "subpath:{sandbox}"
```

- `enforce`: allowed and argument-checked.
- `audit`: inert harness tools, merged from `harness_tools.yaml` (extend with `audit_extra:`).
- `default: deny`: everything else blocked with a receipt.

Constraints: `subpath:`, `shlex:`, `regex:`, `pattern:`, `oneof:`, `exact:`.
`WebFetch` takes a domain allowlist and an optional Cloud approval gate.

## Commands

| Command | Does |
|---------|------|
| `init` | Mint warrant, generate config, wire hooks and `.mcp.json` |
| `up` / `down` | Start / stop the authorizer container |
| `status` | Warrant, authorizer, Cloud, policy summary |
| `doctor [--no-live]` | Self-test allow/deny; optional live hook exit-code check |
| `audit [--tail N]` | Receipt trail |
| `revoke` | Revoke the session warrant |

Tour: `python3 tenuo_demo.py`. Admin: `python3 tenuo_admin.py`.

## Enterprise deployment

Install the hook in managed settings (above user and project settings):

```jsonc
// macOS:   /Library/Application Support/ClaudeCode/managed-settings.json
// Linux:   /etc/claude-code/managed-settings.json
// Windows: C:\ProgramData\ClaudeCode\managed-settings.json
{
  "hooks": {
    "PreToolUse":  [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 /opt/tenuo/tenuo_claude.py _hook"}]}],
    "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 /opt/tenuo/tenuo_claude.py _post"}]}]
  }
}
```

Deploy with MDM alongside the CLI and `tenuo.yaml`.

Governance applies to agent tool calls (`Read`, `Bash`, MCP, subagent spawns).
It does not cover interactive `!` shell in the Claude Code TUI or other paths that
never emit `PreToolUse`. Restrict those at the workstation if they matter for you.

### Tenuo Cloud

With Cloud, warrants are root-signed via a trigger. Admin and runtime use
different keys:

| Tool | Key | Does |
|------|-----|------|
| `tenuo_admin.py setup` | admin (`~/.tenuo/admin.env`) | Register holder, create trigger from `tenuo.yaml` |
| `tenuo_claude.py up` | authorizer (`.state/cloud.env`) | Fire trigger, run authorizer |

Runtime refuses to start if an admin key is present. See `cloud.env.example` and
`tenuo.yaml.cloud.example`.

### Revocation

Cloud: revoke the warrant id from `status` in the dashboard; the authorizer picks
up the SRL within about 30 seconds.

Local: `tenuo-claude revoke` writes a signed SRL and reloads.

## Details

Audit mode (`mode: audit`) computes allow/deny and writes receipts without
blocking. Use it to review `WOULD-DENY` lines, tune policy, then switch to
`enforce`.

WebFetch: allowlisted domains over https plus SSRF checks (metadata, loopback,
encoded IPs, suffix spoof). Off-allowlist URLs are denied unless you enable the
Cloud approval gate.

### Human approval

For off-allowlist URLs that still pass SSRF checks, Cloud can require approver
sign-off before the fetch runs. The prompt goes to whatever channel the approver
identity uses (Telegram, Slack, etc.). Allowlisted hosts and hard SSRF failures
are unchanged. Cloud only.

`python3 tenuo_demo.py --live-approval` runs the full flow. In a live session the
agent blocks on that tool call until someone approves or it times out. Have an
approver ready; Claude's hook timeout can expire first and look like a deny.

### Subagents

Spawn is gated (`spawn_agent` with a `oneof` of role names). Each role gets a
child warrant attenuated from the session warrant. The session is the ceiling.

`doctor` checks that each declared role matches a file under `.claude/agents/`.
You cannot enable `subagents:` and WebFetch `approval` in the same policy today;
the default ships with subagents on.

### Receipts

PreToolUse, PostToolUse, and the MCP proxy all feed the authorizer. Enforced tools
are constraint-checked; audit-listed harness tools are logged; everything else is
default-deny. Subagent calls include `agent_type`.

## Security boundaries

Tenuo controls which tool calls the agent may make. It does not sandbox execution:
a allowed `Bash` or `WebFetch` can still have effects beyond what the argument
check sees. See [The Map is not the Territory](https://niyikiza.com/posts/map-territory/)
for the model.

Claude Code only blocks PreToolUse on exit code 2 or an explicit deny. Exit code
1 is non-blocking, so `_hook` converts internal errors into deny decisions.
`doctor` can run a live check when `claude` is installed (`--no-live` to skip).

Practical limits:

- Bash allowlist checks the command shape, not every path a verb might touch.
- WebFetch checks the URL string, not DNS at connect time.
- The holder key ships with the CLI because Claude cannot sign PoP itself.
- New Claude Code tools default-deny until listed in `harness_tools.yaml`.

## Files

| File | Purpose |
|------|---------|
| `tenuo.yaml` | Policy |
| `harness_tools.yaml` | Bundled harness tool allowlist |
| `tenuo.yaml.cloud.example` | Cloud overlay (approver, optional WebFetch approval) |
| `tenuo_claude_code_architecture.svg` | Diagram |
| `tenuo_claude.py` | CLI, hook, MCP proxy |
| `tenuo_admin.py` | Admin setup |
| `tenuo_demo.py` | Scripted tour |
| `ops_server.py` | Demo MCP server |
| `.claude/agents/researcher.md` | Read-only subagent |
| `sandbox/`, `prod-credentials.env` | In-scope samples; fake decoy outside sandbox |
| `CONTRIBUTING.md` | Maintainer notes |

Maintainer setup: [CONTRIBUTING.md](CONTRIBUTING.md).
