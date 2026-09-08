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
ops/check-foundation.sh
vault/.gitignore
vault/.gitattributes
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
ops/src/vaultops
ops/launchd
ops/tests
ops/tests/fixtures
vault
vault/.vault-bridge
vault/.vault-bridge/protocol
vault/.vault-bridge/requests
vault/.vault-bridge/responses
vault/00_Inbox/Captures
vault/00_Inbox/Imports
vault/01_AI_Review/Pending
vault/01_AI_Review/Resolved
vault/01_AI_Review/Rejected
vault/01_AI_Review/Expired
vault/01_AI_Review/Conflict
vault/10_Journal/Daily
vault/10_Journal/Weekly
vault/10_Journal/Monthly
vault/20_Projects
vault/30_Areas
vault/40_Knowledge/Notes
vault/40_Knowledge/Ideas
vault/40_Knowledge/Questions
vault/40_Knowledge/Sources
vault/40_Knowledge/People
vault/50_Maps
vault/60_Meetings
vault/80_Assets/Inbox
vault/80_Assets/Images
vault/80_Assets/Documents
vault/80_Assets/Audio
vault/90_Archive/Projects
vault/90_Archive/Captures
vault/90_Archive/Other
vault/99_System/Templates
vault/99_System/Bases
vault/99_System/Dashboards
vault/99_System/Schemas
vault/99_System/Scripts/QuickAdd
vault/99_System/CSS
vault/.obsidian-mac
vault/.obsidian-phone
vault/.obsidian-tablet
runtime
runtime/staging
runtime/queue
runtime/quarantine
runtime/awaiting_remote_authorization
runtime/running
runtime/review
runtime/approved
runtime/applying
runtime/done
runtime/rejected
runtime/expired
runtime/failed
runtime/conflict
runtime/runs
runtime/receipts
runtime/locks
runtime/index
runtime/cache
runtime/logs
'

for foundation_path in $foundation_required_files; do
    [ ! -L "$foundation_path" ] || foundation_fail "required file is a symlink: $foundation_path"
    [ -f "$foundation_path" ] || foundation_fail "missing required file: $foundation_path"
done

for foundation_path in $foundation_required_directories; do
    [ ! -L "$foundation_path" ] || foundation_fail "required directory is a symlink: $foundation_path"
    [ -d "$foundation_path" ] || foundation_fail "missing required directory: $foundation_path"
done

for foundation_path in runtime $foundation_required_directories; do
    case "$foundation_path" in
        runtime|runtime/*)
            foundation_mode=$(stat -f '%Lp' "$foundation_path")
            [ "$foundation_mode" = 700 ] || foundation_fail "runtime directory mode must be 0700: $foundation_path is $foundation_mode"
            ;;
    esac
done

[ ! -e bridge ] || foundation_fail 'obsolete top-level bridge/ exists; canonical transport is vault/.vault-bridge/'
grep -Fqx '/vault/' .gitignore || foundation_fail 'control .gitignore must contain /vault/'
grep -Fqx '/runtime/' .gitignore || foundation_fail 'control .gitignore must contain /runtime/'

foundation_gitkeep_matches=$(find vault -name .gitkeep -print)
[ -z "$foundation_gitkeep_matches" ] || foundation_fail "Vault filler .gitkeep files found: $foundation_gitkeep_matches"

for foundation_pattern in \
    '.obsidian-mac/*' \
    '.obsidian-phone/*' \
    '.obsidian-tablet/*' \
    '.obsidian-mac/plugins/' \
    '.obsidian-phone/plugins/' \
    '.obsidian-tablet/plugins/'
do
    grep -Fqx "$foundation_pattern" vault/.gitignore || foundation_fail "Vault profile deny rule missing: $foundation_pattern"
done

foundation_bad_runtime_files=$(find runtime -type f ! -perm 600 -print)
[ -z "$foundation_bad_runtime_files" ] || foundation_fail "runtime files must have mode 0600: $foundation_bad_runtime_files"

foundation_manifest_expected='c5b2ce4408337a5bda9b8dc686c49c3e8df2b331a6a15f06efa3d15427fe896e'
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

foundation_literal_matches=$(find vault ops runtime -type d \( \
    -name YYYY -o -name MM -o -name GGGG -o -name WWW -o \
    -name PROJECT_NAME -o -name JOB_ID \
\) -print)
[ -z "$foundation_literal_matches" ] || foundation_fail "literal placeholder directories found: $foundation_literal_matches"

foundation_symlink_matches=$(find vault ops runtime -type l -print)
[ -z "$foundation_symlink_matches" ] || foundation_fail "unexpected symlinks found: $foundation_symlink_matches"

foundation_control_git=$(git -C "$foundation_root" rev-parse --show-toplevel 2>/dev/null || true)
foundation_vault_git=$(git -C "$foundation_root/vault" rev-parse --show-toplevel 2>/dev/null || true)

if [ -z "$foundation_control_git" ] && [ -z "$foundation_vault_git" ]; then
    printf '%s\n' 'Git boundary: DEFERRED (neither repository has been initialized)'
elif [ -n "$foundation_control_git" ] && [ -n "$foundation_vault_git" ]; then
    [ "$foundation_control_git" = "$foundation_root" ] || foundation_fail "wrong control Git root: $foundation_control_git"
    [ "$foundation_vault_git" = "$foundation_root/vault" ] || foundation_fail "wrong Vault Git root: $foundation_vault_git"
    git -C "$foundation_root" check-ignore --no-index --quiet -- vault || foundation_fail 'control repository must ignore vault/'
    git -C "$foundation_root" check-ignore --no-index --quiet -- runtime || foundation_fail 'control repository must ignore runtime/'
    foundation_tracked_boundaries=$(git -C "$foundation_root" ls-files --stage -- vault runtime)
    [ -z "$foundation_tracked_boundaries" ] || foundation_fail "control index tracks forbidden boundaries: $foundation_tracked_boundaries"
    printf '%s\n' 'Git boundary: PASS (independent control and Vault roots)'
else
    foundation_fail 'only one of the two required Git repositories is initialized'
fi

printf '%s\n' 'Foundation checksum/path checks: PASS'
printf '%s\n' 'Blueprint JSON Schema validation: NOT IMPLEMENTED'
printf '%s\n' 'Cross-document semantic validation: NOT IMPLEMENTED'
printf '%s\n' 'Generated artifact zero-diff validation: NOT IMPLEMENTED'
