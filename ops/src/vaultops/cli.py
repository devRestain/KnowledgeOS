"""Retained import and executable name for the owner interface."""

import sys

from .interfaces import cli as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
