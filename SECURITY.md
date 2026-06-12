# Security

## Reporting vulnerabilities

Please **do not** open a public GitHub issue for security bugs.

Report privately via [GitHub Security Advisories](https://github.com/tenuo-ai/claude-governance/security/advisories/new).
Include steps to reproduce, affected versions, and impact if known.

We aim to acknowledge reports within a few business days.

## Scope

In scope: `tenuo-claude-code` (CLI, hooks, MCP proxy, authorizer integration),
default policy templates, and documented deployment patterns in this repo.

Out of scope: Tenuo Cloud control-plane bugs (report to Tenuo separately), Claude
Code itself, and customer `tenuo.yaml` policies you write locally.

## Secrets and `.state/`

Never commit `.state/` (keys, warrants, `cloud.env`) or real credentials. The demo
includes `demo/prod-credentials.env` as an intentional fake decoy for exfil tests —
do not replace those values with real secrets.
