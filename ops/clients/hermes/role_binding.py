"""Pin a native adapter, its shared role runtime, and the maintained SOUL policy.

The binding identifies a role-capable runtime, not one fictitious provider/model.
Each invocation records its actual selected model and wire reasoning separately.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

HERMES_VERSION = "0.22.5"
HERMES_REVISION = "bf0203a794a07aac3ff764972ab9087baa4e4f4e"


def binding_for(adapter_file, *, control_root=None):
    adapter = Path(adapter_file).resolve()
    directory = adapter.parent
    control = Path(control_root) if control_root is not None else adapter.parents[3]
    spec = importlib.util.spec_from_file_location("knowledgeos_binding_role_policy", directory / "role_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = module.policy_digest(control)
    files = {adapter.name, "native_runtime.py", "agent_runtime.py", "role_policy.py", "role_binding.py"}
    hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in sorted(files)}
    payload = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"runtime_kind": "hermes_native", "hermes_version": HERMES_VERSION,
            "hermes_revision": HERMES_REVISION,
            "adapter_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "agent_policy_digest": policy}
