# Implementation details

How **`tenuo-claude-code`** behaves: policy → warrant → authorizer → hooks/MCP proxy.
Install and day-to-day commands: [README.md](../README.md). Security summary:
[README § Security](../README.md#security). Optional sample project:
[demo/](../demo/).

Trust boundaries: [The Map is not the Territory](https://niyikiza.com/posts/map-territory/).
Report bugs: [SECURITY.md](../SECURITY.md).

## Audit mode (`mode: audit`)

Shadow mode: every call's real allow/deny is still computed against the warrant
and written to the local decision log, but nothing is blocked.

**Neutrality invariant:** in audit mode the hook emits *no* permission decision.
Claude's own permission prompts and settings stay fully in effect. Observe-only
never weakens the stock posture. An explicit hook "allow" would silently
auto-approve calls the user's settings would have prompted on.

Rollout: watch `WOULD-DENY` rows in `audit`, tune policy, then set `mode: enforce`.
The hook reads `mode` live (next tool call); the MCP proxy picks it up on the
next Claude session. `status` shows the active posture.

## Policy refresh (`tenuo-claude refresh`)

After editing warrant-backed policy in `tenuo.yaml` (`enforce`, `default`, `audit_*`,
`subagents`, `mcp`, approval overlay), run **`tenuo-claude refresh`**. It re-mints
the session warrant (or re-fires the Cloud trigger), regenerates `.state/gateway.yaml`,
rewires hooks, and restarts the authorizer if it is already running.

**Cloud trigger:** capabilities in the session warrant still come from the trigger
config on the control plane. Re-run **`tenuo-admin setup`** when those lists change,
then `refresh`.

**Live without refresh:** `mode: audit` / `mode: enforce` only (hook blocking posture).

## Production wiring

Hooks and the MCP proxy invoke `tenuo-claude` on PATH (PyPI install) or
`./bin/tenuo-claude` when developing from a git clone. Project files (`tenuo.yaml`,
`.state/`) live in your governed project directory, not in the package install path.
Discovery: `tenuo.yaml` in cwd or any parent, or set `TENUO_PROJECT_DIR`.

## Why hook and MCP proxy

Both check the same warrant against the same authorizer. The interception point
differs:

| Path | Role |
|------|------|
| MCP proxy | Claude is wired to the proxy, not the downstream server |
| PreToolUse hook | Claude must honor the allow/deny decision |

You could point `.mcp.json` at the downstream server and rely on the hook alone;
policy would be the same. We still ship the proxy because if the hook fails or is
removed, denied MCP calls never reach the server. Native tools have no alternative
interception point, so the hook is mandatory there.

For MCP tools the hook also checks `mcp__…` names against the same `mcp.enforce`
policy the proxy uses.

## WebFetch egress

The structured policy compiles to two checks: `UrlSafe` SSRF hygiene on the raw
`url` (https-only by default, metadata/loopback/encoded-IP blocks), and host
must match the domain allowlist (`*` wildcards ok).

Examples exercised by `verify`:

| URL | Result |
|-----|--------|
| `https://api.github.com`, `https://raw.githubusercontent.com/…` | allow |
| `https://evil.com` | deny (off-allowlist) |
| `http://api.github.com` | deny (plain http) |
| `http://169.254.169.254/latest/meta-data/` | deny (metadata) |
| `http://2130706433/` | deny (decimal-encoded loopback) |
| `http://0x7f000001/` | deny (hex-encoded loopback) |
| `https://api.github.com.evil.com/` | deny (suffix spoof) |
| `https://api.github.com@evil.com/` | deny (userinfo spoof) |

With an approval gate, a call that would otherwise be denied can pause for approver
sign-off instead; SSRF-hygiene denials are still hard-denied and never reach the gate.

URL validation is on the string Claude passes in. DNS rebinding and redirects at
connect time need complementary controls in the fetching process. See the
Map/Territory essay above.

Local minting supports internal-CIDR egress and custom schemes/ports; Cloud
triggers currently enforce the domain allowlist only.

## Human approval (Cloud)

When a session warrant includes **approval gates**, governed tool calls can return
`approval-required` (authorizer code `1707`) instead of a hard allow or deny. The
hook handles this for **any tool** on that path:

1. First authorization attempt → `approval-required` with a request hash.
2. Hook creates a Cloud approval request (holder-signed context attestation).
3. Approver responds on their configured notification channel.
4. Hook re-authorizes with `X-Tenuo-Approvals` carrying Cloud KMS signatures.

Cloud-only: gates carry approver KMS public keys from the linked approval policy.
There is no local fallback. Without Cloud, gated calls that would need sign-off
are denied.

Setup (`tenuo-admin setup`):

1. `cloud.approver_identity_id` names an existing Cloud identity (KMS key +
   notification routing). `cloud.approver_identity` display-name lookup is also
   supported for demos.
2. Setup creates or reuses a Cloud approval policy and bakes `approval_gates` into
   the trigger warrant config (including `_policy_id` for offline verification).
   It also syncs the local gateway and reloads the authorizer when it is already
   running, so new MCP routes (for example `/verify/delete_deployment`) work
   immediately.

Receipts: `PENDING [appr]` while parked, then `ALLOW`/`DENY`. In audit mode the
gate is reported only, never blocks.

**Live approval:** when a gate is configured, the hook blocks on that tool call until
an approver responds (or times out). Ensure the identity is reachable on its
notification channel before testing. Claude's hook timeout can expire first and look
like a deny.

### Example: off-allowlist WebFetch (native hook)

The repo ships `enforce.WebFetch.approval` as a concrete native-hook example. Egress that passes
SSRF checks but is off the domain allowlist:

| URL | Outcome |
|-----|---------|
| allowlisted domain | allowed directly (exempt from gate) |
| off-allowlist, SSRF-safe | paused for approver sign-off |
| SSRF / metadata / loopback / plain-http / suffix-spoof | denied (gate not reached) |

### Example: delete_deployment (MCP proxy)

The advanced overlay adds `mcp.enforce.delete_deployment.approval` so the MCP proxy uses the
same approval workflow as the hook:

| Call | Outcome |
|------|---------|
| `target=staging` | allowed directly (exempt from gate) |
| `target=production` | paused for approver sign-off |
| unlisted MCP tool | denied (capability not granted) |

Policy shape:

```yaml
mcp:
  enforce:
    delete_deployment:
      approval:
        threshold: 1
        exempt:
          target: "exact:staging"
```

Any governed capability can carry an approval gate in the Cloud trigger warrant config the
same way; the hook and MCP proxy both call `authorize_with_approval` on the gated argument.

To test: `tenuo-claude demo --advanced --live-approval` in the [reference demo](../demo/)
when approval is configured in policy.

## Search tools and symlinks

`Glob`/`Grep` search roots are constrained like `Read`. A bare `Grep` with no
path is checked against the hook cwd and denied outside the sandbox. Subpath
arguments are `realpath()`-resolved before authorization, so
`ln -s /etc/passwd sandbox/escape.txt` does not smuggle a read out (`verify`
plants this case). Races between check and open need execution-time guards
(e.g. `path_jail`).

## Warrant TTL and refresh

Session warrants expire (1h TTL). `status` flags `EXPIRED` when lapsed.
`tenuo-claude up` refreshes even while the authorizer is running: Cloud
re-fires the trigger; local re-mints with the same issuer key. No container
restart: the warrant rides in each request header.
Subagent child warrants are
re-derived from the fresh session warrant.

## Bash: `shlex` not `regex`

`Shlex` is structure-aware: rejects pipes, chaining (`&&`/`;`), subshells, and
expansion that `regex:.*` would admit (e.g. `ls && rm -rf /`). A command
allowlist authorizes the verb, not paths. `cat /etc/passwd` can still pass.
Keep Bash to inert commands; scope filesystem with `Read`/`Write`/`Edit`
(`subpath`). For a hard sandbox, drop `Bash` from `enforce`.

## Subagents

When `subagents:` is present:

1. **Spawn gate**: `spawn_agent` with `subagent_type` constrained to declared
   roles. Undeclared spawns are denied by the warrant, not a string check in the hook.
2. **Per-subagent warrant**: each role runs under the session warrant attenuated
   to its `tools`. The session is the ceiling; attenuation is one-way and
   cryptographic.

Omit `subagents:` for flat coverage: spawns are audited, not gated; the subagent
runs under the session warrant.

Roles must match a real `subagent_type` (`.claude/agents/<name>.md` frontmatter
`name:` or a built-in). `verify` and `status` validate this.

**Workflow:** bundled as audit-allow in the package harness list (`src/tenuo_claude_code/data/harness_tools.yaml`). With `subagents:`
declared, inner tool calls from Workflow agents carry an `agent_type` that is
not a declared role. Layer 2 denies them (fail-closed). Workflow is effectively
unusable in subagent mode unless you remove it from the audit list or omit
`subagents:` for flat session coverage.

Changing `subagents:` is a policy change: re-run `tenuo-admin setup` (Cloud) or
`init` (local). Subagents may drop parent approval gates for tools they no longer
hold (e.g. a read-only subagent role without `WebFetch`).

Requires `tenuo` 0.1.0b24+ and authorizer `0.1.0-beta.24` (pinned in `cli.py`).

## Receipts

`PreToolUse` / `PostToolUse` use match-all hooks; MCP tools are also gated by the
proxy. Every governed call is **PoP-signed and checked by the authorizer**. The hook
also appends a **local JSON line** per call:

- **Enforced** tools: constraint-checked; out-of-scope denied.
- **Audit-allowed** harness tools: logged, not blocked.
- **Default-deny** for everything else.

Example local line (`.state/receipts.jsonl`; pretty-printed):

```json
{"phase": "pre", "decision": "deny", "claude_tool": "Bash", "governed": true,
 "args": {"command": "ls && rm -rf /"}, "reason": "Constraint not satisfied"}
```

Subagent calls carry `agent_type`; the hook enforces the child warrant when present.
Spawn is cryptographically gated. In-subagent cap selection depends on Claude Code
populating `agent_type` (see [README § Security](../README.md#security)). Measure overhead
with `tenuo-claude bench`.

In audit mode denials are recorded as `WOULD-DENY` without blocking.

**Authority:** `tenuo-claude audit` pretty-prints `.state/receipts.jsonl` (local
convenience, not signed). **Signed audit receipts** are emitted by the authorizer and
stream to Tenuo Cloud when connected (`signature` + `signing_payload` on each event).
See [README § Receipts](../README.md#receipts).

## Hook exit codes and fail-closed

Claude Code blocks PreToolUse only on exit code **2** or an explicit `deny`.
Exit code **1** (including an unhandled traceback) is non-blocking. The tool
call proceeds. Harness semantics can change between Claude Code releases.

`_hook` wraps its body in a fail-closed guard: internal errors become explicit
deny decisions, never bare exceptions.

`verify --deep` runs a live canary when `claude` is on PATH (`--no-live` to skip). The
test hook writes a marker file before exiting so verify can distinguish "hook
never ran" from "exit 2 did not block."

## Tenuo Cloud (extended)

By default `init` mints from a local issuer key. With
[Cloud](https://cloud.tenuo.ai), warrants are issued by the tenant root via a
trigger: the pattern most organizations use for production: one audit stream,
central revocation, admin/runtime key separation, and optional org-wide hook
deployment via managed settings (policy enforced outside Claude's permission UI).

Admin registers the holder and creates the trigger; runtime only fires
it. Runtime refuses to start if an admin key is reachable. First trigger fire
locks to the discovered runtime service account.

Revocation: Cloud revokes by warrant id; authorizer pulls SRL within ~30s. Local:
`tenuo-claude revoke` writes a signed SRL and reloads.
