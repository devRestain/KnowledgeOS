from __future__ import annotations

import re
import tomllib
from pathlib import Path

CONTROL_ROOT = Path(__file__).resolve().parents[2]
OPS_ROOT = CONTROL_ROOT / "ops"


def test_mise_and_image_pin_the_same_python_and_uv_versions() -> None:
    mise = tomllib.loads((CONTROL_ROOT / "mise.toml").read_text(encoding="utf-8"))
    dockerfile = (OPS_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert mise["tools"] == {"python": "3.12.8", "uv": "0.8.14"}
    assert 'FROM python:3.12.8-slim-bookworm' in dockerfile
    assert 'ARG UV_VERSION=0.8.14' in dockerfile
    assert 'org.knowledgeos.python-version="3.12.8"' in dockerfile
    assert 'org.knowledgeos.uv-version="0.8.14"' in dockerfile


def test_project_rejects_host_python_older_than_312() -> None:
    pyproject = (OPS_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12"' in pyproject
    assert not re.search(r"requires-python\s*=\s*\">=3\\.[01]", pyproject)


def test_compose_requires_the_invoking_host_uid_and_gid() -> None:
    makefile = (CONTROL_ROOT / "Makefile").read_text(encoding="utf-8")
    compose = (OPS_ROOT / "compose.yaml").read_text(encoding="utf-8")
    dockerfile = (OPS_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "KNOWLEDGEOS_UID ?= $(shell id -u)" in makefile
    assert "KNOWLEDGEOS_GID ?= $(shell id -g)" in makefile
    assert "${KNOWLEDGEOS_UID:-1000}" not in compose
    assert "${KNOWLEDGEOS_GID:-1000}" not in compose
    assert "${KNOWLEDGEOS_UID:?" in compose
    assert "${KNOWLEDGEOS_GID:?" in compose
    assert "ARG KNOWLEDGEOS_UID=1000" not in dockerfile
    assert "ARG KNOWLEDGEOS_GID=1000" not in dockerfile


def test_make_and_compose_parameterize_independent_host_root_sources() -> None:
    makefile = (CONTROL_ROOT / "Makefile").read_text(encoding="utf-8")
    compose = (OPS_ROOT / "compose.yaml").read_text(encoding="utf-8")
    test_compose = (OPS_ROOT / "compose.test.yaml").read_text(encoding="utf-8")

    assert (
        "export KNOWLEDGEOS_CONTROL_SOURCE KNOWLEDGEOS_VAULT_SOURCE KNOWLEDGEOS_RUNTIME_SOURCE KNOWLEDGEOS_STATE_SOURCE"
        in makefile
    )
    for name in (
        "KNOWLEDGEOS_CONTROL_SOURCE",
        "KNOWLEDGEOS_VAULT_SOURCE",
        "KNOWLEDGEOS_RUNTIME_SOURCE",
        "KNOWLEDGEOS_STATE_SOURCE",
    ):
        assert f"source: ${{{name}:?" in compose
    assert "KNOWLEDGEOS_CONTROL_SOURCE ?= $(abspath $(dir $(lastword $(MAKEFILE_LIST))))" in makefile
    assert "KNOWLEDGEOS_VAULT_SOURCE ?= $(KNOWLEDGEOS_CONTROL_SOURCE)/KnowledgeHub" not in makefile
    assert "KNOWLEDGEOS_RUNTIME_SOURCE ?= $(KNOWLEDGEOS_CONTROL_SOURCE)/runtime" not in makefile
    assert (
        "source: ${KNOWLEDGEOS_CONTROL_SOURCE:?Set KNOWLEDGEOS_CONTROL_SOURCE through Make}"
        in test_compose
    )
    assert "target: /workspace/control" in compose
    assert "target: /workspace/KnowledgeHub" in compose
    assert "target: /workspace/runtime" in compose
    assert "target: /workspace/state" in compose
    assert compose.count("create_host_path: false") == 5
