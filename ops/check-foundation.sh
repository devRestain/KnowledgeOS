#!/bin/sh

set -eu

foundation_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
foundation_root=$(CDPATH= cd -- "$foundation_script_dir/.." && pwd)
cd "$foundation_root"

foundation_fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

foundation_required_files='
.gitignore
.gitattributes
AGENTS.md
README.md
Makefile
OBSIDIAN_VAULT_BLUEPRINT.md
OBSIDIAN_VAULT_WHITEPAPER.md
blueprint/README.md
blueprint/CHECKSUMS.sha256
blueprint/blueprint.yaml
blueprint/blueprint.schema.json
docs/ARCHITECTURE.md
docs/OPERATIONS.md
docs/MOBILE.md
docs/RUNTIME.md
docs/DECISIONS.md
docs/IMPLEMENTATION_STATUS.md
docs/SOURCE_CONTRACT.md
ops/AGENTS.md
ops/check-foundation.sh
ops/compose.test.yaml
ops/config/generated-artifacts.yaml
ops/tests/AGENTS.md
'

foundation_required_directories='
blueprint
docs
ops
ops/config
ops/actions
ops/schemas
ops/prompts
ops/policies
ops/expected
ops/src/vaultops
ops/launchd
ops/tests
ops/tests/fixtures
ops/tests/support
'

for foundation_path in $foundation_required_files; do
    [ ! -L "$foundation_path" ] || foundation_fail "required control file is a symlink: $foundation_path"
    [ -f "$foundation_path" ] || foundation_fail "missing required control file: $foundation_path"
done

for foundation_path in $foundation_required_directories; do
    [ ! -L "$foundation_path" ] || foundation_fail "required control directory is a symlink: $foundation_path"
    [ -d "$foundation_path" ] || foundation_fail "missing required control directory: $foundation_path"
done

[ ! -e bridge ] || foundation_fail 'obsolete control path exists: bridge/'
grep -Fqx '/KnowledgeHub/' .gitignore || foundation_fail 'control .gitignore must contain /KnowledgeHub/'
grep -Fqx '/runtime/' .gitignore || foundation_fail 'control .gitignore must contain /runtime/'

foundation_manifest_expected='8766e8f920c8861119bd39d16f0ffa47f668e7503d06a2ababb55d44994167ce'
foundation_manifest_actual=$(shasum -a 256 blueprint/CHECKSUMS.sha256 | awk '{print $1}')
[ "$foundation_manifest_actual" = "$foundation_manifest_expected" ] || foundation_fail "checksum manifest hash mismatch: expected=$foundation_manifest_expected actual=$foundation_manifest_actual"

shasum -a 256 -c blueprint/CHECKSUMS.sha256

ruby -ryaml -rjson -e '
  blueprint = YAML.safe_load(File.read("blueprint/blueprint.yaml"), aliases: true)
  schema = JSON.parse(File.read("blueprint/blueprint.schema.json"))
  abort "unexpected contract_id" unless blueprint["contract_id"] == "knowledgeos-blueprint-v2"
  abort "schema root must be an object" unless schema.is_a?(Hash)
  abort "top-level required/key count mismatch" unless Array(schema["required"]).length == blueprint.keys.length
'

foundation_literal_matches=$(find ops -type d \( \
    -name YYYY -o -name MM -o -name GGGG -o -name WWW -o \
    -name PROJECT_NAME -o -name JOB_ID \
\) -print)
[ -z "$foundation_literal_matches" ] || foundation_fail "literal placeholder directories found in control source: $foundation_literal_matches"

foundation_symlink_matches=$(find ops -type l -print)
[ -z "$foundation_symlink_matches" ] || foundation_fail "unexpected symlinks found in control source: $foundation_symlink_matches"

foundation_control_git=$(git -C "$foundation_root" rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$foundation_control_git" ]; then
    printf '%s\n' 'Git boundary: DEFERRED (control repository has not been initialized)'
else
    [ "$foundation_control_git" = "$foundation_root" ] || foundation_fail "wrong control Git root: $foundation_control_git"
    git -C "$foundation_root" check-ignore --no-index --quiet -- KnowledgeHub/.knowledgeos-boundary-probe || foundation_fail 'control repository must ignore KnowledgeHub/'
    git -C "$foundation_root" check-ignore --no-index --quiet -- runtime/.knowledgeos-boundary-probe || foundation_fail 'control repository must ignore runtime/'
    foundation_tracked_boundaries=$(git -C "$foundation_root" ls-files --stage -- KnowledgeHub runtime)
    [ -z "$foundation_tracked_boundaries" ] || foundation_fail "control index tracks forbidden boundaries: $foundation_tracked_boundaries"
    printf '%s\n' 'Git boundary: PASS (control root excludes mutable Vault and runtime boundaries)'
fi

printf '%s\n' 'Control foundation checksum/path checks: PASS'
printf '%s\n' 'Blueprint JSON Schema validation: AVAILABLE via make blueprint-check'
printf '%s\n' 'Cross-document semantic validation: AVAILABLE via make blueprint-check'
printf '%s\n' 'Control generated-artifact validation: AVAILABLE via make schema-check'
