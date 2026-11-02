"""Explicit read-only Owner composition; no execution authorities or background effects."""

from __future__ import annotations

import argparse
import os
import sys

from ..adapters.core import AdmissionError
from ..adapters.paths import ResolvedPaths, resolve_beneath, resolve_paths
from ..application.exops import KnowledgeExOps, OwnerActor
from ..application.knowledge import KnowledgeApplication, LocalIdentity
from ..projection import ProjectionError, load_current_projection
from .loopback_owner import create_loopback_owner


def compose_read_only_owner(roots: ResolvedPaths, *, actor_id: str) -> KnowledgeExOps:
    """Check existing roots, journal pins and Lexical sources without creating anything."""
    if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 128:
        raise AdmissionError("BINDING_INVALID", "Owner needs a trusted startup actor")
    if not all(getattr(roots, kind).is_dir()
               for kind in ("core", "control", "vault", "state", "runtime")):
        raise AdmissionError("ROOT_MISSING", "Read-only Owner requires five existing roots")
    if not resolve_beneath(roots.state, "owner-control.json").is_file():
        raise AdmissionError("STATE_MISSING", "Read-only Owner requires an initialized owner journal")
    application = KnowledgeApplication.from_roots(
        roots, identity=LocalIdentity(actor_id, "operator", local_execution=True))
    try:
        load_current_projection(roots, verify_sources=True)
    except ProjectionError as error:
        raise AdmissionError("INDEX_STALE", "Read-only Owner requires a current Lexical generation") from error
    actor = OwnerActor(actor_id, "web")
    exops = KnowledgeExOps(
        application, read_only=True,
        authorize_actor=lambda selected, action: selected == actor
        and action in {"read_work", "search_query"})
    exops._guard(actor, "read_work")
    return exops


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor-id", required=True, help="Trusted startup owner login, not a request field")
    parser.add_argument("--bind-host", choices=("127.0.0.1", "owner"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17900)
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        roots = resolve_paths()
        exops = compose_read_only_owner(roots, actor_id=arguments.actor_id)
        server = create_loopback_owner(
            exops, actor_id=arguments.actor_id, port=arguments.port, bind_host=arguments.bind_host)
        with server:
            print("KnowledgeOS read-only Owner ready", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError) as error:
        # Do not export roots, request data or arbitrary exception text.
        print(f"KnowledgeOS read-only Owner refused: {getattr(error, 'code', 'STARTUP_FAILED')}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
