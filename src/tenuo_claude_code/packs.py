"""Bundled policy packs for common agent/tool surfaces.

The MVP intentionally keeps packs local and boring: reviewed metadata plus a
small renderer. Signing/remote registries can come later without changing the
first-run experience.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from string import Template

import yaml

PACKAGE = "tenuo_claude_code"


@dataclass(frozen=True)
class Pack:
    name: str
    version: int
    title: str
    reviewed: str
    reviewed_by: str
    pinned: dict
    description: str
    params: list[dict]
    summary: dict
    assumptions: list[str]
    path: resources.abc.Traversable

    @property
    def display_version(self) -> str:
        return f"v{self.version}"


def _packs_root() -> resources.abc.Traversable:
    return resources.files(PACKAGE).joinpath("packs")


def _pack_dir(name: str) -> resources.abc.Traversable:
    return _packs_root().joinpath(name)


def load_pack(name: str) -> Pack:
    pdir = _pack_dir(name)
    meta = pdir.joinpath("pack.yaml")
    if not meta.is_file():
        raise SystemExit(f"Unknown policy pack '{name}'. Run `tenuo-claude pack list`.")
    data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
    return Pack(
        name=str(data["name"]),
        version=int(data["version"]),
        title=str(data.get("title") or data["name"]),
        reviewed=str(data["reviewed"]),
        reviewed_by=str(data["reviewed_by"]),
        pinned=dict(data.get("pinned") or {}),
        description=str(data.get("description") or ""),
        params=list(data.get("params") or []),
        summary=dict(data.get("summary") or {}),
        assumptions=[str(v) for v in (data.get("assumptions") or [])],
        path=pdir,
    )


def list_packs() -> list[Pack]:
    root = _packs_root()
    out: list[Pack] = []
    if not root.is_dir():
        return out
    for child in root.iterdir():
        if child.is_dir() and child.joinpath("pack.yaml").is_file():
            out.append(load_pack(child.name))
    return sorted(out, key=lambda p: p.name)


def render_pack(pack: Pack, params: dict[str, str]) -> str:
    values = {k: str(v) for k, v in params.items()}
    values.setdefault("pack_name", pack.name)
    values.setdefault("pack_version", pack.display_version)
    values.setdefault("reviewed", pack.reviewed)
    values.setdefault("reviewed_by", pack.reviewed_by)
    values.setdefault("pinned_name", str(pack.pinned.get("name", "")))
    values.setdefault("pinned_version", str(pack.pinned.get("version", "")))
    values.setdefault("tool_list_hash", str(pack.pinned.get("tool_list_hash", "")))
    template = pack.path.joinpath("tenuo.yaml.tmpl")
    if not template.is_file():
        raise SystemExit(f"Pack '{pack.name}' is missing tenuo.yaml.tmpl")
    return Template(template.read_text(encoding="utf-8")).substitute(values)


def collect_params(pack: Pack, supplied: dict[str, str], *, assume_defaults: bool = False) -> dict[str, str]:
    out: dict[str, str] = {}
    for param in pack.params:
        name = str(param["name"])
        if name in supplied:
            out[name] = supplied[name]
            continue
        default = str(param.get("default") or "")
        if assume_defaults and default:
            out[name] = default
            continue
        prompt = str(param.get("prompt") or name)
        suffix = f" ({param.get('example')})" if param.get("example") else ""
        try:
            answer = input(f"  -> {prompt}{suffix}: ").strip()
        except EOFError:
            answer = ""
        answer = answer or default
        if not answer and param.get("required", True):
            raise SystemExit(f"Pack '{pack.name}' needs parameter '{name}'.")
        out[name] = answer
    return out


def parse_param_overrides(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--param expects name=value, got {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"--param expects name=value, got {item!r}")
        out[key] = value.strip()
    return out


def write_pack_policy(project_root: Path, pack: Pack, params: dict[str, str], *, force: bool = False) -> Path:
    dest = project_root / "tenuo.yaml"
    if dest.exists() and not force:
        raise SystemExit(f"{dest.name} already exists. Re-run with --force to overwrite.")
    dest.write_text(render_pack(pack, params), encoding="utf-8")
    return dest

