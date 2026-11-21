"""Owner configuration: bound paths and settings (Blueprint §2, §7)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import OPERATION_ID

ROOT = Path(__file__).resolve().parents[2]
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
DEFAULT_PORT = 8765


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class OwnerConfig:
    """Paths the Owner binds plus a few settings.

    ``root`` is the control root (this checkout: catalog, roles, frontend). Vault, State
    and Runtime are bound separately and must not alias or nest each other.
    ``settings`` overrides per-capability catalog settings: ``{"C2": {"quiet_period_minutes": 5}}``.
    """

    vault: Path
    state: Path
    runtime: Path
    root: Path = ROOT
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    operation_id: str = OPERATION_ID
    instance_id: str = "knowledgeos-local"
    workers: int = 4
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("vault", "state", "runtime", "root"):
            object.__setattr__(self, name, Path(getattr(self, name)).expanduser().resolve())
        if self.host not in LOOPBACK_HOSTS:
            raise ConfigError(f"ExOps binds loopback only, not {self.host!r}")
        bound = {"vault": self.vault, "state": self.state, "runtime": self.runtime}
        for left, left_path in bound.items():
            for right, right_path in bound.items():
                if left != right and (left_path == right_path or left_path.is_relative_to(right_path)):
                    raise ConfigError(f"{left} must not alias or nest inside {right}")

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    def with_port(self, port: int) -> OwnerConfig:
        return replace(self, port=port)


def from_dict(data: dict[str, Any], base: Path | None = None) -> OwnerConfig:
    """Build a config from JSON-like data; relative paths resolve against ``base``."""
    base = base or Path.cwd()
    known = {name for name in OwnerConfig.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(sorted(unknown))}")
    values = dict(data)
    for name in ("vault", "state", "runtime"):
        if name not in values:
            raise ConfigError(f"config needs {name!r}")
    for name in ("vault", "state", "runtime", "root"):
        if name in values:
            path = Path(values[name]).expanduser()
            values[name] = path if path.is_absolute() else base / path
    return OwnerConfig(**values)


def load(path: str | Path) -> OwnerConfig:
    path = Path(path).expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read config {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    return from_dict(data, base=path.parent)
