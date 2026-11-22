#!/usr/bin/env python3
"""Build the KnowledgeOS namespace in the independent Sites monorepo."""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def main() -> int:
    fabric_root = Path(os.environ.get("AGENTFABRIC_ROOT", Path(__file__).resolve().parents[3]))
    sites_root = Path(os.environ.get("SITES_ROOT", fabric_root / "Sites"))
    sys.path.insert(0, str(sites_root))
    module = runpy.run_path(str(sites_root / "build_sites.py"))
    return module["main"](["--fabric-root", str(fabric_root), "--operation", "knowledgeos", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
