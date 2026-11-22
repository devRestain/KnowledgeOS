#!/usr/bin/env python3
"""Check the KnowledgeOS namespace without regenerating other Sites objects."""
from __future__ import annotations

import sys
from build_wiki import main


if __name__ == "__main__":
    sys.argv.append("--check")
    raise SystemExit(main())
