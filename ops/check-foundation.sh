#!/bin/sh
set -eu
# Run through Make in the owned container; fixed Blueprint trust and Git
# boundaries are checked by the maintained Python foundation implementation.
exec python -c 'from pathlib import Path; from vaultops.foundation import check_foundation; p=check_foundation(Path("/workspace/control")); print("Control foundation checksum/path checks: PASS" if not p else "\n".join(p)); raise SystemExit(bool(p))'
