## PyPI release

CI builds on every push/PR; **Release** publishes on tag `v*` or manual dispatch.

### One-time: GitHub secret (required)

The release workflow uses `uv publish` with **`PYPI_TOKEN`** — it does **not** use OIDC
trusted publishing (that was failing with `invalid-publisher`).

Add the secret to **this** repository (Settings → Secrets → Actions):

```bash
# Paste the same PyPI upload token used for tenuo-ai/tenuo (Settings → Secrets → PYPI_TOKEN)
gh secret set PYPI_TOKEN --repo tenuo-ai/claude-governance
```

The token needs upload scope for the **`tenuo-claude-code`** project on PyPI.

### Publish

```bash
gh workflow run release.yml -R tenuo-ai/claude-governance
```

Or push a tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Install after publish:

```bash
pip install tenuo-claude-code   # CLI command: tenuo-claude
```

Verify: https://pypi.org/project/tenuo-claude-code/

### Cut a new version

Bump `version` in `pyproject.toml` and `src/tenuo_claude_code/__init__.py`, then tag:

```bash
git tag v0.1.1
git push origin v0.1.1
gh release create v0.1.1 --title "tenuo-claude 0.1.1" --notes "..."
```
