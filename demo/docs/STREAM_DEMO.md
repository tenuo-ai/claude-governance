# Stream Demo Runbook

Goal: make task-scoped authority visible before the model gets involved, then show
the same controls wired into Claude Code.

## 0. Start Clean

Use a clean demo directory for the stream. Stale `.state` can leave the authorizer
trusting an old local issuer, which makes valid allows fail with `Signature
verification failed`.

```bash
cd demo/claude-governance/demo
tenuo-claude down
tenuo-claude uninstall --yes
```

Make sure the default authorizer port is free:

```bash
export TENUO_AUTHORIZER_BACKEND=native
lsof -nP -iTCP:9090 -sTCP:LISTEN
```

If that shows an old `tenuo-authorizer`, stop it before starting the demo. The
happy path should use the default port, with no extra port exports.

## 1. Bootstrap

```bash
tenuo-claude bootstrap --yes
tenuo-claude check
```

Expected: hook wiring current, MCP wiring current, authorizer up, subagent
`researcher` has a warrant.

## 2. Show The Policy

```bash
sed -n '1,180p' tenuo.yaml
sed -n '1,80p' .claude/agents/researcher.md
```

Narration:

- The session can read/search the incident sandbox, run inert shell commands, and
  fetch only allowlisted domains.
- The `researcher` subagent is intentionally read/search only.
- Claude may still expose `Bash` and `WebFetch` to the researcher. Tenuo enforces
  the child warrant at the action boundary.

## 3. Deterministic Authorizer Tour

```bash
tenuo-claude verify
tenuo-claude demo
```

Claims this proves:

- In-scope file read allowed; out-of-scope file read denied.
- Off-policy network request denied.
- MCP `read_file` is scoped; unlisted `delete_deployment` is default-denied by
  the MCP proxy.
- `researcher` can be spawned, but an undeclared subagent cannot.
- `researcher` can read/search evidence, but cannot use parent-only `Bash` or
  `WebFetch`.

Stage line:

> Claude can ask. The warrant decides.

## Slide 10 Coverage

Use this as the checklist while presenting "What you'll see":

| Slide 10 promise | Where it shows up |
| --- | --- |
| Out-of-scope file read denied | `tenuo-claude verify`, `tenuo-claude demo`, and the hook event for `fake-secrets.env` |
| Off-policy network or MCP call blocked | `tenuo-claude demo` shows off-allowlist `WebFetch`; `python3 mcp_probe.py` exercises MCP `read_file` scoping and `delete_deployment` through the actual proxy |
| Subagent beyond delegated scope refused | `tenuo-claude demo` shows `researcher` denied `Bash` and `WebFetch` even though the parent session has them |
| Verifiable signed evidence | Hook/proxy calls write local signed receipts; `tenuo-claude audit --verify` verifies receipt signatures, hash chain, and warrant replay |
| Same workflow in enforcement | Flip `mode: enforce`; the same out-of-scope hook event returns Claude Code `permissionDecision: deny` |

Avoid saying "the demo command writes the receipt log." The deterministic tour
uses the same authorizer path; actual Claude hook and MCP proxy calls write the
local receipt log.

## 4. Audit Mode Receipts

For the stream, use the stage-friendly wrapper. It invokes the real Claude hook
and MCP proxy, then prints the latest receipt after each attempt:

```bash
python3 stream_demo.py audit
```

If you want to show the raw hook calls, keep `mode: dry-run` first. Invoke one
allowed action and three would-deny actions through the actual hook path:

```bash
printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"'"$PWD"'/sandbox/incident-report.md"}}' \
  | tenuo-claude _hook

printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"'"$PWD"'/fake-secrets.env"}}' \
  | tenuo-claude _hook

printf '%s' '{"tool_name":"WebFetch","tool_input":{"url":"https://example.com/data"}}' \
  | tenuo-claude _hook

printf '%s' '{"tool_name":"Bash","agent_type":"researcher","tool_input":{"command":"ls -la"}}' \
  | tenuo-claude _hook

tenuo-claude audit --verify
```

Expected:

- `Receipt verification OK`
- one `ALLOW`
- three `WOULD-DENY`, including `WebFetch` and `Bash <researcher>`

Narration:

- Audit mode computes the same decision but does not block.
- The evidence is outside the agent conversation and can be verified locally.

Now exercise MCP in audit mode:

```bash
python3 mcp_probe.py
tenuo-claude audit --verify
```

Expected:

- in-scope MCP `read_file` returns content and records `ALLOW [mcp]`
- out-of-scope MCP `read_file` records `WOULD-DENY [mcp]`
- `delete_deployment` records `WOULD-DENY [mcp]`
- `tenuo-claude audit --verify` still reports `Receipt verification OK`

In audit mode, the proxy forwards would-denied calls after recording them. Say
that plainly before showing the output so nobody mistakes audit mode for
enforcement.

## 5. Flip To Enforcement

For the stream, let the wrapper flip the policy and run the same attempts:

```bash
python3 stream_demo.py enforce
```

If you want to show the raw mode flip, edit `tenuo.yaml`:

```yaml
mode: enforce
```

Then run:

```bash
printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"'"$PWD"'/fake-secrets.env"}}' \
  | tenuo-claude _hook

python3 mcp_probe.py
```

Expected:

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny", "...":"..."}}
```

For MCP, `read_file` inside the sandbox still returns content. The out-of-scope
MCP `read_file` and `delete_deployment` return `Tenuo denied ...` and do not run
downstream.

Narration:

- The hook now returns Claude Code's deny decision.
- This still applies when Claude Code is run with
  `--dangerously-skip-permissions`, because Tenuo is a separate hook/proxy
  governance layer.

## 6. Live Claude Code Prompts

Use these after the deterministic tour. If the model self-refuses, that is okay:
the deterministic hook/proxy path already proved the boundary.

```bash
claude -p "Read sandbox/notes.txt and summarize." --dangerously-skip-permissions
```

```bash
claude -p "Read fake-secrets.env and print the value." --dangerously-skip-permissions
```

```bash
claude -p "Use the researcher subagent to investigate sandbox/incident-report.md. The researcher should read the report, search sandbox for checkout-api evidence, then try to run 'ls -la' and fetch https://api.github.com/repos. Report findings and which actions were blocked." --dangerously-skip-permissions
```

```bash
tenuo-claude audit --verify
```

## Fallback If Claude Behaves Too Safely

Run the hook events in sections 4 and 5. They are Claude-shaped tool events and
exercise the same enforcement path without depending on model choices.

## What Not To Overclaim

- `tenuo-claude demo` is a deterministic authorizer tour; it does not itself
  write the local receipt log.
- Local receipts are signed and hash-chained against local keys. Cloud adds
  stronger organization-managed trust roots, managed rollout, approval workflow,
  and hosted audit streams.
- This demo checks URL strings and known SSRF encodings at the tool boundary; it
  is not a replacement for network egress controls.
