"""Align the existing convenience homes with committed roles; preview unless --apply.

Only config.yaml and the composed SOUL.md are changed. Authentication, history,
Gateway transports and Cron are never read or operated. Explicit Hermes execution
uses its managed YAML API; project-container execution uses its existing PyYAML.
"""
from __future__ import annotations

import argparse
import copy
import json
import site
import sys
from pathlib import Path

from role_policy import load_role, policy_digest

_READ_TOOLS = ["index_status_read", "knowledge_search", "knowledge_retrieve",
               "knowledge_read", "citation_verify"]


def bootstrap_packages(hermes_root: Path) -> None:
    """Load managed dependencies without installing or activating any runtime."""
    root = hermes_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Hermes source is unavailable")
    sys.path.insert(0, str(root))
    from pm.environments import committed_venv, site_packages

    environment = committed_venv(root)
    if (environment is None or sys.prefix == sys.base_prefix
            or Path(sys.prefix).resolve() != environment.resolve()):
        raise ValueError("Select the committed Hermes venv/bin/python")
    packages = site_packages(environment)
    if not packages.is_dir():
        raise ValueError("Committed Hermes dependencies are unavailable")
    site.addsitedir(str(packages))


def alignment(control_root: Path, profiles_root: Path, *, yaml) -> list[dict]:
    """Prepare every home before writing any, retaining unrelated private settings."""

    manifest = json.loads((control_root / "ops/config/agent-profiles.json").read_bytes())
    planned = []
    for home_name, role_id in manifest["home_roles"].items():
        home = profiles_root / home_name
        config_path = home / "config.yaml"
        soul_path = home / "SOUL.md"
        if (home.is_symlink() or not home.is_dir() or config_path.is_symlink()
                or not config_path.is_file() or soul_path.is_symlink()):
            raise ValueError("Expected existing regular profile home/config/SOUL")
        before = config_path.read_bytes()
        config = yaml.safe_load(before)
        if not isinstance(config, dict):
            raise TypeError("Expected a profile configuration object")
        config = copy.deepcopy(config)
        role = load_role(control_root, role_id)
        if role["provider"] != "openai-codex":
            raise ValueError("Convenience homes require their committed Codex route")
        config["model"] = {"default": role["model"], "provider": role["provider"],
                           "base_url": role["base_url"]}
        config.pop("fallback_model", None)
        config.setdefault("agent", {})["reasoning_effort"] = role["reasoning_effort"]
        config.setdefault("curator", {})["enabled"] = False
        config.setdefault("tools", {}).setdefault("tool_search", {})["enabled"] = "off"
        if home_name == "kos-cited-answer":
            server = config.get("mcp_servers", {}).get("knowledgeos", {})
            command = server.get("command", "")
            if (not isinstance(command, str) or not Path(command).is_absolute()
                    or Path(command).name != "knowledgeos-mcp-readonly"
                    or server.get("args", []) != []):
                raise ValueError("Expected the existing fixed read-only MCP launcher")
            config["mcp_servers"] = {"knowledgeos": {
                "command": command, "args": [], "connect_timeout": 30,
                "tools": {"include": list(_READ_TOOLS), "resources": False, "prompts": False},
            }}
            toolset = "knowledgeos"
        else:
            config["mcp_servers"] = {}
            toolset = "no_mcp"
        config.setdefault("platform_toolsets", {})["cli"] = [toolset]
        after = yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=2**31 - 1).encode("utf-8")
        soul = role["soul"].encode("utf-8")
        changed = []
        # Avoid reformatting an already aligned private configuration.
        if yaml.safe_load(before) != config:
            changed.append((config_path, after))
        if not soul_path.is_file() or soul_path.read_bytes() != soul:
            changed.append((soul_path, soul))
        planned.append({"home": home_name, "role_id": role_id,
                        "route": role["route_name"], "provider": role["provider"],
                        "model": role["model"], "reasoning_effort": role["reasoning_effort"],
                        "prompt_digest": role["prompt_digest"], "writes": changed})
    return planned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--profiles-root", type=Path, required=True)
    parser.add_argument("--hermes-root", type=Path,
                        help="Load packages from this source's committed Hermes environment")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.hermes_root is not None:
            bootstrap_packages(args.hermes_root)
            import hermes_yaml as yaml
        else:
            import yaml
    except (OSError, ValueError, ImportError, RuntimeError):
        print(json.dumps({"error": "PROFILE_ALIGNMENT_FAILED",
                          "message": "Select the committed Hermes interpreter and dependency environment"}))
        return 1
    try:
        digest = policy_digest(args.control_root)
        planned = alignment(args.control_root, args.profiles_root, yaml=yaml)
        for item in planned:
            if args.apply:
                for path, payload in item["writes"]:
                    path.write_bytes(payload)
        summary = [{**{key: value for key, value in item.items() if key != "writes"},
                    "changed_files": [path.name for path, _ in item["writes"]]}
                   for item in planned]
        print(json.dumps({"mode": "applied" if args.apply else "preview",
                          "agent_policy_digest": digest, "homes": summary}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
        # Parser diagnostics may contain private YAML values; never echo them.
        print(json.dumps({"error": "PROFILE_ALIGNMENT_FAILED",
                          "message": "Check existing homes, fixed read-only launcher and committed policy"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
