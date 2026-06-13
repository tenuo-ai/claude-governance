# Images for docs

| File | Used in | Shows |
|------|---------|--------|
| `tenuo_claude_code_architecture.png` | README (PyPI + GitHub) | Architecture diagram (export from `.svg`) |
| `tenuo_claude_code_architecture.svg` | Source | Editable diagram |
| `cloud-audit-stream.png` | README | Cloud receipt list (allow/deny/approved) |
| `cloud-receipt-approval-detail.png` | PRESENTATION | Drill-down: human approval + request hash |

**PyPI:** the project README uses absolute `raw.githubusercontent.com` URLs and PNG
(raster) assets. PyPI does not resolve relative paths and does not render SVG in
project descriptions.

Regenerate the architecture PNG after editing the SVG:

```bash
rsvg-convert -w 1280 docs/images/tenuo_claude_code_architecture.svg \
  -o docs/images/tenuo_claude_code_architecture.png
```

Captured from [cloud.tenuo.ai](https://cloud.tenuo.ai) after a governed WebFetch
with approval configured. Do not commit credentials or secrets in replacements.
