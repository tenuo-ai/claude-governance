# Policy templates

Illustrative `tenuo.yaml` patterns for common setups. **Not audited for your
environment.** Pick a template, save it as `tenuo.yaml` in your **project
directory** (the folder where you run `tenuo-claude init` and open Claude Code),
then tune sandbox paths, domains, and tool lists.

| Template | Use case |
|----------|----------|
| [read-only-research.yaml](read-only-research.yaml) | Read/search only; read-only subagent |
| [enforce-with-mcp.yaml](enforce-with-mcp.yaml) | Sandbox + inert Bash + scoped MCP tools |
| [audit-rollout.yaml](audit-rollout.yaml) | Observe-only rollout (`mode: audit`) before enforce |

Minimal starter (no subagents/MCP): [tenuo.yaml.example](../../templates/tenuo.yaml.example).

Full reference project with tour fixtures: [demo/](../../demo/).

## Use a template

```bash
mkdir my-project && cd my-project
curl -fsSL https://raw.githubusercontent.com/tenuo-ai/claude-governance/main/examples/policies/read-only-research.yaml -o tenuo.yaml
# edit name, sandbox, domains, MCP downstream, etc.
tenuo-claude init && tenuo-claude up && tenuo-claude verify
```

Open Claude Code in `my-project/` (same directory as `tenuo.yaml`).

## Contribute a template

PRs welcome for **use-case** templates (not production exports):

- Generic names (`ci-agent.yaml`, not `acme-corp-prod.yaml`)
- No real hostnames, approver identities, or org-specific paths
- Short comment header: what it allows, what it denies, local vs Cloud notes
- Run `tenuo-claude verify` against the template if you can (optional but helpful)

We review templates like code. End users deploy by saving an adapted file as
`tenuo.yaml` in each governed project directory (teams often store that file in
version control alongside their codebase).
