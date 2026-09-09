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
