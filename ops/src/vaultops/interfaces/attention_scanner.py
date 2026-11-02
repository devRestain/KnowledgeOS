"""One explicit owner attention reconciliation cycle over committed Work records.

Run in the selected installed owner environment with all five roots configured:
``python -m vaultops.interfaces.attention_scanner --binding host_module:compose``.
The trusted host startup module's ``compose(journal)`` returns OwnerAttention,
with an exact current owner export resolver and DiscordAttentionBinding. It owns
credentials and route policy; this command accepts neither webhook URLs nor text.
Nothing runs on import and no Activity or Work result depends on this process.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys

from ..adapters.core import AdmissionError
from ..adapters.paths import resolve_paths
from ..application.knowledge import KnowledgeApplication
from ..application.owner_attention import OwnerAttention


def scan_committed_owner(attention: OwnerAttention) -> dict:
    """Concrete scanner caller, also usable by the explicitly bound supervisor."""
    if not isinstance(attention, OwnerAttention):
        raise AdmissionError("ATTENTION_DENIED", "Scanner requires an exact owner attention binding")
    return attention.reconcile_committed_work()


def private_failure_boundary(_kind, _error, _traceback) -> None:
    """CLI unhandled-error presentation; never render private host exceptions.

    Programming faults still escape main and terminate unsuccessfully. Embedding
    callers own their equivalent public HTTP/CLI presentation boundary.
    """
    print(json.dumps({"status": "failed", "code": "ATTENTION_UNEXPECTED"}), file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", required=True, help="Trusted startup module:function, not an HTTP/request field")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        module, separator, function = args.binding.partition(":")
        if not separator or not module or not function.isidentifier():
            raise AdmissionError("ATTENTION_DENIED", "Exact trusted binding module:function is required")
        application = KnowledgeApplication.from_roots(resolve_paths())
        attention = getattr(importlib.import_module(module), function)(application.journal)
        if not isinstance(attention, OwnerAttention) or attention.journal is not application.journal:
            raise AdmissionError("ATTENTION_DENIED", "Binding must use the selected owner journal")
        print(json.dumps(scan_committed_owner(attention), sort_keys=True))
    except (AdmissionError, OSError, ImportError, AttributeError, ValueError):
        # Expected startup/configuration failures may contain credential URLs.
        print(json.dumps({"status": "denied", "code": "ATTENTION_UNAVAILABLE"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.excepthook = private_failure_boundary
    raise SystemExit(main())
