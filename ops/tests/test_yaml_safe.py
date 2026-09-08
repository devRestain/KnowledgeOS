from __future__ import annotations

import pytest
import yaml

from vaultops.yaml_safe import DuplicateKeyError, load_yaml_text


def test_duplicate_mapping_keys_are_rejected() -> None:
    with pytest.raises(DuplicateKeyError, match="duplicate YAML key 'name'"):
        load_yaml_text("name: first\nname: second\n")


def test_implicit_timestamps_remain_strings() -> None:
    value = load_yaml_text(
        "date_only: 2026-09-08\n"
        "date_time: 2026-09-08T13:00:00+09:00\n"
    )
    assert value == {
        "date_only": "2026-09-08",
        "date_time": "2026-09-08T13:00:00+09:00",
    }
    assert all(isinstance(item, str) for item in value.values())


def test_safe_loader_keeps_expected_scalar_types() -> None:
    assert load_yaml_text("enabled: true\ncount: 2\nname: demo\n") == {
        "enabled": True,
        "count": 2,
        "name": "demo",
    }


def test_python_object_tags_are_rejected() -> None:
    with pytest.raises(yaml.YAMLError):
        load_yaml_text("!!python/object/apply:os.system ['echo unsafe']\n")
