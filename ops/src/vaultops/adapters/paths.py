"""Strict independent roots; configuration is private binding, not authority."""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

EXIT_INPUT_INVALID = 10
EXIT_CONFIG_INVALID = 11
ROOT_KINDS = ("core", "control", "vault", "state", "runtime")
CONFIG_KEYS = frozenset(
    {"schema_version", "project_name", "timezone", *(f"{kind}_root" for kind in ROOT_KINDS)}
)


class RootResolutionError(ValueError):
    """Display-safe failure raised before consumption or mutation."""

    def __init__(self, code: str, message: str, *, exit_code: int = 10, locator: str = "/root") -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.locator = locator


@dataclass(frozen=True, slots=True)
class ResolvedPaths:
    core: Path
    control: Path
    vault: Path
    state: Path
    runtime: Path
    config_file: Path
    config: Mapping[str, Any]
    sources: Mapping[str, str]


def _config_error(code: str, message: str, locator: str = "/config") -> RootResolutionError:
    return RootResolutionError(code, message, exit_code=EXIT_CONFIG_INVALID, locator=locator)


def _path(value: object, *, base: Path) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not os.fspath(value).strip():
        raise RootResolutionError("ROOT_PATH_INVALID", "root must name a non-empty path")
    selected = Path(value).expanduser()
    return selected if selected.is_absolute() else base / selected


def _no_symlink(path: Path, *, code: str = "ROOT_SYMLINK") -> None:
    # Inspect original components before resolve() can hide a parent symlink.
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            raise RootResolutionError(code, "selected path crosses a symlink")


def load_config_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    selected = _path(path, base=Path.cwd())
    try:
        _no_symlink(selected, code="CONFIG_FILE_SYMLINK")
        with selected.open("rb") as handle:
            config = tomllib.load(handle)
    except RootResolutionError:
        raise _config_error("CONFIG_FILE_SYMLINK", "config must not cross a symlink") from None
    except FileNotFoundError:
        raise _config_error("CONFIG_FILE_MISSING", "configure independent roots using schema v3") from None
    except (OSError, tomllib.TOMLDecodeError):
        raise _config_error("CONFIG_INVALID", "config could not be read or parsed") from None
    if type(config.get("schema_version")) is not int or config["schema_version"] != 3:
        raise _config_error(
            "CONFIG_VERSION_UNSUPPORTED",
            "only config v3 is supported; declare Core, control, Vault, State and Runtime separately",
            "/config/schema_version",
        )
    if set(config) != CONFIG_KEYS:
        raise _config_error("CONFIG_SCHEMA_INVALID", "config v3 key set is invalid")
    if config["project_name"] != "KnowledgeOS":
        raise _config_error("CONFIG_PROJECT_INVALID", "config project identity is invalid")
    if config["timezone"] != "Asia/Seoul":
        raise _config_error("CONFIG_DRIFT", "config timezone differs from the project timezone")
    for kind in ROOT_KINDS:
        value = config[f"{kind}_root"]
        if not isinstance(value, str) or not value.strip():
            raise _config_error("CONFIG_PATH_INVALID", f"{kind}_root must name a non-empty path")
    return config


def _validate_root(path: Path, kind: str) -> Path:
    _no_symlink(path)
    selected = path.resolve(strict=False)
    if selected.exists():
        if not selected.is_dir():
            raise RootResolutionError("ROOT_INVALID", f"{kind} root must be a directory")
        if kind in {"state", "runtime"} and stat.S_IMODE(selected.stat().st_mode) != 0o700:
            raise RootResolutionError(f"ROOT_{kind.upper()}_MODE", f"{kind} root must have mode 0700")
    elif kind not in {"state", "runtime"} or not selected.parent.is_dir():
        raise RootResolutionError("ROOT_MISSING", f"{kind} root or its parent is missing")
    if kind in {"state", "runtime"} and ((selected / ".git").exists() or (selected / ".git").is_symlink()):
        raise RootResolutionError(f"ROOT_{kind.upper()}_GIT", f"{kind} root must remain outside Git")
    return selected


def resolve_paths(
    control_root: str | os.PathLike[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> ResolvedPaths:
    """Resolve explicit config and private overrides without creating paths."""

    env = os.environ if environment is None else environment
    initial = _path(
        control_root if control_root is not None else env.get("KNOWLEDGEOS_CONTROL_ROOT", "/workspace/control"),
        base=Path.cwd(),
    )
    _no_symlink(initial)
    config_path = _path(env.get("KNOWLEDGEOS_CONFIG_FILE", str(initial / "ops/vaultops.toml")), base=initial)
    config = load_config_file(config_path)
    roots: dict[str, Path] = {}
    sources: dict[str, str] = {}
    for kind in ROOT_KINDS:
        name = f"KNOWLEDGEOS_{kind.upper()}_ROOT"
        if kind == "control" and control_root is not None:
            value, source = control_root, "cli:--root"
        elif name in env:
            value, source = env[name], f"environment:{name}"
        else:
            value, source = config[f"{kind}_root"], f"config:{kind}_root"
        roots[kind] = _validate_root(_path(value, base=config_path.parent), kind)
        sources[kind] = source
    for index, left in enumerate(ROOT_KINDS):
        for right in ROOT_KINDS[index + 1 :]:
            a, b = roots[left], roots[right]
            if a == b or a in b.parents or b in a.parents:
                raise RootResolutionError("ROOT_OVERLAP", f"{left} and {right} roots must be independent")
    values = dict(config)
    values.update({f"{kind}_root": str(path) for kind, path in roots.items()})
    sources["config"] = "environment:KNOWLEDGEOS_CONFIG_FILE" if "KNOWLEDGEOS_CONFIG_FILE" in env else "owner_config_v3"
    return ResolvedPaths(
        **roots,
        config_file=config_path.resolve(),
        config=MappingProxyType(values),
        sources=MappingProxyType(sources),
    )


def resolve_api_paths(control_root: str | os.PathLike[str] | ResolvedPaths) -> ResolvedPaths:
    """Keep trusted resolved contexts intact; never derive legacy roots."""

    return control_root if isinstance(control_root, ResolvedPaths) else resolve_paths(control_root)


def resolve_beneath(root: str | os.PathLike[str], relative: str | os.PathLike[str]) -> Path:
    raw = os.fspath(relative)
    if not raw or raw.startswith("/") or "\\" in raw or any(part in {"", ".", ".."} for part in raw.split("/")):
        raise RootResolutionError("ROOT_PATH_UNSAFE", "child path must be bounded relative POSIX text")
    base = Path(root)
    _no_symlink(base)
    candidate = base.joinpath(*raw.split("/"))
    _no_symlink(candidate, code="ROOT_PATH_UNSAFE")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(base.resolve()):
        raise RootResolutionError("ROOT_PATH_UNSAFE", "child path leaves its owner root")
    return resolved
