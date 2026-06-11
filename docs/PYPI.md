## PyPI release

CI builds on every push/PR; **Release** publishes on tag `v*` or manual dispatch.

### One-time: GitHub secret

Add `PYPI_TOKEN` to this repo (Settings → Secrets → Actions). Use the same
project-scoped token as the main `tenuo` PyPI account, with upload scope for
`tenuo-claude-code`.

Alternatively configure [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/)
for `tenuo-ai/claude-governance` / `.github/workflows/release.yml` and re-enable
`id-token: write` on the release workflow.

### Cut a release

```bash
# bump version in pyproject.toml + src/tenuo_claude_code/__init__.py first
git tag v0.1.1
git push origin v0.1.1
gh release create v0.1.1 --title "tenuo-claude-code 0.1.1" --notes "..."
```

Or: Actions → Release → Run workflow.

Verify: https://pypi.org/project/tenuo-claude-code/
