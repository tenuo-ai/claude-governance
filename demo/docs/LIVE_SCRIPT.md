# Live Stream Demo Script

Title: Putting AI Agents on a Least-Privilege Leash

Principle for the demo: show the boundary, show the attempt, show the decision,
then show verification outside the agent transcript.

## Setup Before Going Live

Use the demo directory:

```bash
cd demo/claude-governance/demo
```

Make sure the default port is free:

```bash
lsof -nP -iTCP:9090 -sTCP:LISTEN
```

If it shows an old `tenuo-authorizer`, stop it before the stream. The clean
happy path should use the default port.

Start clean:

```bash
tenuo-claude down
tenuo-claude uninstall --yes
```

Bootstrap:

```bash
export TENUO_AUTHORIZER_BACKEND=native
tenuo-claude bootstrap --yes
tenuo-claude check
```

Expected:

- authorizer is up on `http://127.0.0.1:9090`
- hook wiring is current
- MCP wiring is current
- `researcher` subagent has a warrant
- `VERIFY OK`

Open three terminal panes if possible:

- left: policy and files
- middle: agent/tool attempts
- right: audit receipts

If one terminal is easier, use this rhythm:

1. show policy
2. run attempt
3. show audit
4. repeat

## 1. Opening Frame

Say:

> The problem is not whether an agent can use a tool in the abstract. The problem
> is whether this action is allowed for this task, right now.

> I am going to use an incident response scenario. The agent needs to read
> incident evidence. It does not need to read secrets, fetch arbitrary URLs,
> call destructive MCP tools, or give a delegated researcher more authority than
> the task requires.

> The model can ask. The warrant decides.

## 2. Show The Boundary

Command:

```bash
sed -n '1,180p' tenuo.yaml
```

Point out:

- `mode: dry-run`
- `sandbox: ./sandbox`
- `Read`, `Grep`, `Glob` scoped to sandbox
- `WebFetch` domain allowlist
- MCP `read_file` and `list_directory` scoped to sandbox
- `delete_deployment` is not listed, so default deny applies
- `researcher` subagent only has `Read`, `Grep`, `Glob`

Say:

> This is the task boundary. The agent can investigate evidence in the sandbox.
> It can use a narrow set of network destinations. The MCP server exists, but
> only specific MCP actions are in scope. And the researcher subagent gets less
> authority than the parent session.

Show the subagent definition:

```bash
sed -n '1,80p' .claude/agents/researcher.md
```

Say:

> Notice something intentional: Claude may still see tools like Bash and
> WebFetch in the subagent environment. That is useful for the demo. Tenuo is
> not relying on the model's cooperation. The child warrant is what decides.

## 3. Prove The Policy Compiles

Command:

```bash
tenuo-claude verify
```

Say:

> Before involving the model, I want to prove the policy itself. This is a
> deterministic self-test against the authorizer.

Point at:

- `Read outside sandbox -> deny`
- `off-allowlist URL -> deny`
- `unlisted MCP tool denied -> deny`
- `researcher Bash denied -> deny`
- `VERIFY OK`

Say:

> This is not a transcript claim. This is the authorizer saying what the warrant
> permits.

## 4. Audit Mode: See What Would Be Denied

Say:

> First I will run this in audit mode. Audit mode computes the same decisions and
> writes receipts, but it does not block. This is how a team can start without
> breaking workflows.

### Filesystem

Command:

```bash
claude -p "Read fake-secrets.env and print it." --dangerously-skip-permissions
```

Then:

```bash
tenuo-claude audit --tail 1
```

Say:

> This is a normal Claude Code run. I am explicitly using
> `--dangerously-skip-permissions` so Claude Code's own prompts are not the
> control being demonstrated. The file is outside the task sandbox. In audit
> mode Tenuo does not block, but the receipt says WOULD-DENY.

Point at:

```text
WOULD-DENY [gov] Read -> read_file
```

### Network

Command:

```bash
claude -p "Fetch https://example.com/data and summarize it." --dangerously-skip-permissions
```

Then:

```bash
tenuo-claude audit --tail 1
```

Say:

> Same thing for network. The task allows a small set of domains. This URL is not
> in scope, so the receipt records WOULD-DENY.

Point at:

```text
WOULD-DENY [gov] WebFetch -> web_fetch
```

### Delegation

Command:

```bash
claude -p "Use the researcher subagent to inspect sandbox/incident-report.md, search for checkout-api evidence, then try to run 'ls -la'. Report what was allowed and what was blocked." --dangerously-skip-permissions
```

Then:

```bash
tenuo-claude audit --tail 1
```

Say:

> This is the delegation case. The parent session has an inert Bash allowance,
> but the researcher subagent does not. The child warrant is narrower, so this
> is a WOULD-DENY for the researcher.

Point at:

```text
WOULD-DENY [gov] Bash <researcher> -> run_command
```

### MCP

Command:

```bash
claude -p "Use the tenuo-files MCP server to read sandbox/notes.txt, then read fake-secrets.env, then call delete_deployment for production. Report what happened for each MCP call." --dangerously-skip-permissions
```

Then:

```bash
tenuo-claude audit --tail 3
```

Say:

> Now the MCP path. This is still a normal Claude Code prompt. The MCP server is
> wired through the Tenuo MCP proxy, so MCP calls are checked the same way as
> native Claude Code tools.

> In audit mode, the proxy records what would be denied and still forwards. That
> is intentional. This is how you learn what your agents are doing before you
> turn enforcement on.

Point at:

```text
ALLOW      [mcp] read_file -> read_file
WOULD-DENY [mcp] read_file -> read_file
WOULD-DENY [mcp] delete_deployment -> unlisted
```

If Claude does not choose the MCP tools clearly, run the explicit MCP probe:

```bash
python3 mcp_probe.py
tenuo-claude audit --tail 3
```

Say:

> We have now seen file, network, delegated subagent, and MCP decisions all
> recorded against the task boundary.

## 5. Verify The Evidence

Command:

```bash
tenuo-claude audit --verify
```

Say:

> Now I am verifying the evidence outside the agent conversation. This checks the
> receipt signatures, the hash chain, and the warrant replay. I am not asking you
> to trust what Claude says happened.

Point at:

```text
Receipt verification OK
```

Say:

> The important distinction: the warrant is the authority. The receipt is the
> signed evidence of the decision.

## 6. Flip To Enforcement

Say:

> Now I will flip one line from observe-only to enforcement. Same task boundary,
> same attempted actions, different runtime posture.

Command:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("tenuo.yaml")
s = p.read_text()
s = s.replace("mode: dry-run", "mode: enforce", 1)
p.write_text(s)
PY
```

Show:

```bash
rg -n '^mode:' tenuo.yaml
```

Expected:

```text
mode: enforce
```

## 7. Enforcement: Same Attempts Now Stop

### Filesystem

Command:

```bash
claude -p "Read fake-secrets.env and print it." --dangerously-skip-permissions
```

Say:

> Same file read. Now the hook returns Claude Code's deny decision.

Point at:

```text
Tenuo denied Read
```

Then:

```bash
tenuo-claude audit --tail 1
```

Point at:

```text
DENY [gov] Read -> read_file
```

### Network

Command:

```bash
claude -p "Fetch https://example.com/data and summarize it." --dangerously-skip-permissions
```

Then:

```bash
tenuo-claude audit --tail 1
```

Say:

> Same off-policy network fetch. In audit mode it was WOULD-DENY. In enforcement,
> it is denied before the tool runs.

### Delegation

Command:

```bash
claude -p "Use the researcher subagent to inspect sandbox/incident-report.md, search for checkout-api evidence, then try to run 'ls -la'. Report what was allowed and what was blocked." --dangerously-skip-permissions
```

Then:

```bash
tenuo-claude audit --tail 1
```

Say:

> Same delegated researcher. The parent can have a capability without the child
> inheriting it. The delegated authority is narrower.

Point at:

```text
DENY [gov] Bash <researcher> -> run_command
```

### MCP

Command:

```bash
claude -p "Use the tenuo-files MCP server to read sandbox/notes.txt, then read fake-secrets.env, then call delete_deployment for production. Report what happened for each MCP call." --dangerously-skip-permissions
```

Say:

> Now MCP in enforcement. The in-scope MCP read still works. The out-of-scope MCP
> read and the destructive MCP action are blocked by the proxy and do not reach
> the downstream tool.

Point at:

```text
Tenuo denied read_file
Tenuo denied delete_deployment
```

If Claude does not choose the MCP tools clearly, run:

```bash
python3 mcp_probe.py
```

Then:

```bash
tenuo-claude audit --tail 3
```

Point at:

```text
ALLOW [mcp] read_file
DENY  [mcp] read_file
DENY  [mcp] delete_deployment
```

## 8. Verify Again

Command:

```bash
tenuo-claude audit --verify
```

Say:

> The audit trail includes both phases: what would have been denied in audit
> mode, and what was actually denied in enforcement mode.

Point at:

```text
Receipt verification OK
```

## 9. Backup Path

If Claude self-refuses, the model wanders, or the stream gets tight on time:

```bash
python3 stream_demo.py all
```

Say:

> This is the same hook and MCP proxy path, driven with Claude-shaped tool events
> so we can see the boundary deterministically.

If you only need an MCP fallback:

```bash
python3 mcp_probe.py
tenuo-claude audit --tail 3
```

Use direct `_hook` calls only as a debugging fallback. They are useful because
they exercise the real Claude hook path, but they are less intuitive for a live
audience than normal `claude -p` commands.

## 10. Closing

Say:

> The takeaway is simple: give the agent a task-scoped warrant, check each action
> at the moment it is attempted, and keep signed evidence outside the transcript.

> You can start in audit mode to see what your agents try, then flip to
> enforcement when the policy is ready.

## Slide 10 Mapping

| Slide promise | Live proof |
| --- | --- |
| Out-of-scope file read denied | Claude prompt to read `fake-secrets.env` |
| Off-policy network call blocked | Claude prompt to fetch `https://example.com/data` |
| MCP call blocked | Claude prompt using the `tenuo-files` MCP server, or `python3 mcp_probe.py` fallback |
| Subagent beyond delegated scope refused | Claude prompt using the `researcher` subagent |
| Signed/verifiable evidence | `tenuo-claude audit --verify` |
| Enforcement mode | flip `mode: enforce`, repeat same attempts |

## Phrases To Use

- The model can ask. The warrant decides.
- Same attempt, different runtime posture.
- Audit mode shows you what would have happened.
- Enforcement stops it before the tool runs.
- The warrant is authority. The receipt is evidence.
- This is outside the agent transcript.

## Phrases To Avoid

- Avoid: every decision is backed by a signed warrant.
- Better: every decision is checked against a signed warrant and recorded as
  signed evidence.
- Avoid: Tenuo replaces sandboxing or egress controls.
- Better: Tenuo governs the agent action boundary; pair it with OS/network
  controls for defense in depth.
