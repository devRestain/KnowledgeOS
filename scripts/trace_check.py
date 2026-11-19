"""Check docs/CAPABILITY_TRACE.md against the acceptance examples in docs/CAPABILITIES.md.

Every acceptance ID must have exactly one trace row with an implementation location and
at least one pytest node ID that exists in the source tree. Prints the node IDs on success
so they can be passed to `make test TESTS=...`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_RE = re.compile(r"^- \*\*([A-Z]+-\d+)\*\* ", re.MULTILINE)
ROW_RE = re.compile(r"^\|\s*([A-Z]+-\d+)\s*\|(.*)\|\s*$", re.MULTILINE)
NODE_RE = re.compile(r"`(tests/[^`:]+\.py::[A-Za-z_][\w]*)`")
IMPL_RE = re.compile(r"`(src/knowledgeos/[^`:]+\.py|thin-client/[^`:]+|frontend/[^`:]+)(?:::([\w.]+))?`")


def main() -> int:
    spec = (ROOT / "docs/CAPABILITIES.md").read_text()
    trace = (ROOT / "docs/CAPABILITY_TRACE.md").read_text()
    expected = ACCEPTANCE_RE.findall(spec)
    errors: list[str] = []
    if len(expected) != len(set(expected)):
        errors.append("duplicate acceptance IDs in CAPABILITIES.md")
    rows: dict[str, str] = {}
    for match in ROW_RE.finditer(trace):
        acceptance_id, cells = match.group(1), match.group(2)
        if acceptance_id in rows:
            errors.append(f"{acceptance_id}: more than one trace row")
        rows[acceptance_id] = cells
    missing = [i for i in expected if i not in rows]
    extra = [i for i in rows if i not in expected]
    if missing:
        errors.append(f"missing rows: {', '.join(missing)}")
    if extra:
        errors.append(f"rows without acceptance example: {', '.join(extra)}")
    nodes: list[str] = []
    for acceptance_id in expected:
        cells = rows.get(acceptance_id)
        if cells is None:
            continue
        if re.search(r"(?i)unimplemented|todo|tbd|not implemented", cells):
            errors.append(f"{acceptance_id}: marked unimplemented")
        impls = IMPL_RE.findall(cells)
        if not impls:
            errors.append(f"{acceptance_id}: no implementation location")
        for path, symbol in impls:
            source = ROOT / path
            if not source.is_file():
                errors.append(f"{acceptance_id}: implementation file missing {path}")
            elif symbol and not re.search(rf"\b(def|class)\s+{re.escape(symbol.split('.')[-1])}\b|\b{re.escape(symbol.split('.')[-1])}\b", source.read_text()):
                errors.append(f"{acceptance_id}: symbol {symbol} not in {path}")
        row_nodes = NODE_RE.findall(cells)
        if not row_nodes:
            errors.append(f"{acceptance_id}: no test node ID")
        for node in row_nodes:
            path, function = node.split("::")
            source = ROOT / path
            if not source.is_file() or not re.search(rf"^def {re.escape(function)}\(", source.read_text(), re.MULTILINE):
                errors.append(f"{acceptance_id}: test node missing {node}")
            nodes.append(node)
    print(f"acceptance examples: {len(expected)}; trace rows: {len(rows)}; test nodes: {len(set(nodes))}")
    if errors:
        print("\n".join(f"ERROR {error}" for error in errors))
        return 1
    print(" ".join(dict.fromkeys(nodes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
