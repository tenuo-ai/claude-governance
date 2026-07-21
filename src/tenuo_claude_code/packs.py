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


def _pack_schema_error(name: str, message: str) -> None:
    raise SystemExit(f"Invalid policy pack '{name}': {message}")


def _require_mapping(data: dict, field: str, pack_name: str) -> dict:
    if field not in data:
        _pack_schema_error(pack_name, f"missing required field '{field}'")
    value = data[field]
    if not isinstance(value, dict):
        _pack_schema_error(pack_name, f"field '{field}' must be a mapping")
    return value


def _optional_mapping(data: dict, field: str, pack_name: str) -> dict:
    value = data.get(field, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        _pack_schema_error(pack_name, f"field '{field}' must be a mapping")
    return value


def _require_str(data: dict, field: str, pack_name: str) -> str:
    if field not in data:
        _pack_schema_error(pack_name, f"missing required field '{field}'")
    value = data[field]
    if not isinstance(value, str) or not value.strip():
        _pack_schema_error(pack_name, f"field '{field}' must be a non-empty string")
    return value.strip()


def _optional_str(data: dict, field: str, pack_name: str, default: str = "") -> str:
    value = data.get(field, default)
    if value is None:
        return default
    if not isinstance(value, str):
        _pack_schema_error(pack_name, f"field '{field}' must be a string")
    return value


def _require_int(data: dict, field: str, pack_name: str) -> int:
    if field not in data:
        _pack_schema_error(pack_name, f"missing required field '{field}'")
    value = data[field]
    if isinstance(value, bool) or not isinstance(value, int):
        _pack_schema_error(pack_name, f"field '{field}' must be an integer")
    return value


def _optional_str_list(data: dict, field: str, pack_name: str) -> list[str]:
    value = data.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list):
        _pack_schema_error(pack_name, f"field '{field}' must be a list")
    out: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            _pack_schema_error(pack_name, f"field '{field}[{idx}]' must be a string")
        out.append(item)
    return out


def _optional_params(data: dict, pack_name: str) -> list[dict]:
    value = data.get("params", [])
    if value is None:
        return []
    if not isinstance(value, list):
        _pack_schema_error(pack_name, "field 'params' must be a list")
    out: list[dict] = []
    for idx, raw in enumerate(value):
        if not isinstance(raw, dict):
            _pack_schema_error(pack_name, f"field 'params[{idx}]' must be a mapping")
        param = dict(raw)
        _require_str(param, "name", pack_name)
        for field in ("prompt", "example", "default"):
            if field in param and param[field] is not None and not isinstance(param[field], str):
                _pack_schema_error(
                    pack_name, f"field 'params[{idx}].{field}' must be a string")
        if "required" in param and not isinstance(param["required"], bool):
            _pack_schema_error(pack_name, f"field 'params[{idx}].required' must be a boolean")
        out.append(param)
    return out


def _validate_pack_data(name: str, raw) -> dict:
    if raw is None:
        _pack_schema_error(name, "pack.yaml is empty")
    if not isinstance(raw, dict):
        _pack_schema_error(name, "pack.yaml must contain a mapping")

    pack_name = _require_str(raw, "name", name)
    if pack_name != name:
        _pack_schema_error(name, f"field 'name' must match directory name '{name}'")
    pinned = _require_mapping(raw, "pinned", name)
    return {
        "name": pack_name,
        "version": _require_int(raw, "version", name),
        "title": _optional_str(raw, "title", name, pack_name) or pack_name,
        "reviewed": _require_str(raw, "reviewed", name),
        "reviewed_by": _require_str(raw, "reviewed_by", name),
        "pinned": {
            "name": _require_str(pinned, "name", name),
            "version": _require_str(pinned, "version", name),
            "tool_list_hash": _require_str(pinned, "tool_list_hash", name),
        },
        "description": _optional_str(raw, "description", name),
        "params": _optional_params(raw, name),
        "summary": _optional_mapping(raw, "summary", name),
        "assumptions": _optional_str_list(raw, "assumptions", name),
    }


def _packs_root() -> resources.abc.Traversable:
    return resources.files(PACKAGE).joinpath("packs")


def _pack_dir(name: str) -> resources.abc.Traversable:
    return _packs_root().joinpath(name)


def load_pack(name: str) -> Pack:
    pdir = _pack_dir(name)
    meta = pdir.joinpath("pack.yaml")
    if not meta.is_file():
        raise SystemExit(f"Unknown policy pack '{name}'. Run `tenuo-claude pack list`.")
    data = _validate_pack_data(name, yaml.safe_load(meta.read_text(encoding="utf-8")))
    template = pdir.joinpath("tenuo.yaml.tmpl")
    if not template.is_file():
        raise SystemExit(f"Invalid policy pack '{name}': missing required file 'tenuo.yaml.tmpl'")
    return Pack(
        name=data["name"],
        version=data["version"],
        title=data["title"],
        reviewed=data["reviewed"],
        reviewed_by=data["reviewed_by"],
        pinned=data["pinned"],
        description=data["description"],
        params=data["params"],
        summary=data["summary"],
        assumptions=data["assumptions"],
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
