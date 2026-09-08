"""Fail-closed YAML loading for control files.

PyYAML's SafeLoader still applies YAML's implicit timestamp resolver and accepts
duplicate mapping keys unless a constructor overrides that behaviour. Both are
unsafe for a contract loader: a date-looking scalar must remain the user's
string, and silently choosing the last duplicate would hide a configuration
mistake.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class YAMLContractError(ValueError):
    """Base error for rejected control YAML."""


class DuplicateKeyError(YAMLContractError):
    """Raised when one mapping contains a key more than once."""


class NonStringKeyError(YAMLContractError):
    """Raised when a control mapping uses a non-string key."""


class _ContractLoader(yaml.SafeLoader):
    pass


_ContractLoader.yaml_implicit_resolvers = {
    initial: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag != "tag:yaml.org,2002:timestamp"
    ]
    for initial, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _construct_mapping(loader: _ContractLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise NonStringKeyError(f"mapping key must be a string at line {key_node.start_mark.line + 1}")
        if key in mapping:
            raise DuplicateKeyError(
                f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_ContractLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def load_yaml_text(text: str) -> Any:
    """Load one safe YAML document without implicit timestamp conversion."""

    return yaml.load(text, Loader=_ContractLoader)


def load_yaml_file(path: str | Path) -> Any:
    """Read UTF-8 YAML and load it through :func:`load_yaml_text`."""

    file_path = Path(path)
    return load_yaml_text(file_path.read_text(encoding="utf-8"))
