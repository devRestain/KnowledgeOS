"""Load committed KnowledgeOS role prompts and model routes without runtime access.

All source files are read as raw bytes, without newline or Unicode normalization.
A composed prompt is COMMON.md bytes + one LF byte + the role SOUL.md bytes,
strictly decoded as UTF-8; prompt_digest is SHA-256 of those composed bytes.
policy_digest covers the raw manifest and every referenced prompt, including
COMMON.md, once in sorted control-relative POSIX path order. Each entry is its
UTF-8 path, a NUL byte, an unsigned eight-byte big-endian payload length, and its
raw payload. All returned digests use the sha256:<lowercase hex> convention.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_MANIFEST = "ops/config/agent-profiles.json"
_COMMON = "ops/prompts/hermes/COMMON.md"


def _manifest(control_root: Path) -> tuple[dict, bytes]:
    raw = (control_root / _MANIFEST).read_bytes()
    manifest = json.loads(raw)
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported agent policy schema version")
    return manifest, raw


def load_role(control_root: Path, role_id: str, *, route: str | None = None) -> dict:
    """Return the composed role prompt and its explicitly permitted model route.

    Unknown roles, unknown routes, and routes outside the role's allowed_routes
    raise ValueError. There is no model substitution or access to installed homes.
    """
    manifest, _ = _manifest(control_root)
    if role_id not in manifest["profiles"]:
        raise ValueError(f"Unknown agent role: {role_id}")
    profile = manifest["profiles"][role_id]
    selected = profile["route"] if route is None else route
    if selected not in manifest["routes"]:
        raise ValueError(f"Unknown agent route: {selected}")
    allowed = profile["allowed_routes"]
    if selected not in allowed:
        raise ValueError(f"Agent route is not allowed for role: {role_id}")
    prompt = ((control_root / _COMMON).read_bytes() + b"\n"
              + (control_root / profile["soul"]).read_bytes())
    return {
        **manifest["routes"][selected],
        "role_id": role_id,
        "soul": prompt.decode("utf-8"),
        "prompt_digest": "sha256:" + hashlib.sha256(prompt).hexdigest(),
        "route_name": selected,
        "allowed_routes": list(allowed),
    }


def policy_digest(control_root: Path) -> str:
    """Bind the raw manifest and all consumed prompt files, not credentials/State."""
    manifest, raw = _manifest(control_root)
    paths = {_COMMON, *(profile["soul"] for profile in manifest["profiles"].values())}
    files = {_MANIFEST: raw}
    files.update((path, (control_root / path).read_bytes()) for path in paths)
    digest = hashlib.sha256()
    for path, payload in sorted(files.items()):
        digest.update(path.encode("utf-8") + b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()
