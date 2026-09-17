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
ops/config/generated-artifacts.yaml
KnowledgeHub/.gitignore
KnowledgeHub/.gitattributes
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
KnowledgeHub
KnowledgeHub/.vault-bridge
KnowledgeHub/.vault-bridge/protocol
KnowledgeHub/.vault-bridge/requests
KnowledgeHub/.vault-bridge/responses
KnowledgeHub/00_Inbox/Captures
KnowledgeHub/01_AI_Review/Pending
KnowledgeHub/01_AI_Review/Resolved
KnowledgeHub/01_AI_Review/Rejected
KnowledgeHub/01_AI_Review/Expired
KnowledgeHub/01_AI_Review/Conflict
KnowledgeHub/10_Journal/Daily
KnowledgeHub/10_Journal/Weekly
KnowledgeHub/10_Journal/Monthly
KnowledgeHub/20_Projects
KnowledgeHub/30_Areas
KnowledgeHub/40_Knowledge/Notes
KnowledgeHub/40_Knowledge/Ideas
KnowledgeHub/40_Knowledge/Questions
KnowledgeHub/40_Knowledge/Sources
KnowledgeHub/40_Knowledge/People
KnowledgeHub/50_Maps
KnowledgeHub/60_Meetings
KnowledgeHub/80_Assets/Inbox
KnowledgeHub/80_Assets/Images
KnowledgeHub/80_Assets/Documents
KnowledgeHub/80_Assets/Audio
KnowledgeHub/90_Archive/Projects
KnowledgeHub/90_Archive/Captures
KnowledgeHub/90_Archive/Other
KnowledgeHub/99_System/Templates
KnowledgeHub/99_System/Bases
KnowledgeHub/99_System/Dashboards
KnowledgeHub/99_System/Schemas
KnowledgeHub/99_System/Scripts/QuickAdd
KnowledgeHub/99_System/CSS
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

[ ! -e bridge ] || foundation_fail 'obsolete top-level bridge/ exists; canonical transport is KnowledgeHub/.vault-bridge/'
grep -Fqx '/KnowledgeHub/' .gitignore || foundation_fail 'control .gitignore must contain /KnowledgeHub/'
grep -Fqx '/runtime/' .gitignore || foundation_fail 'control .gitignore must contain /runtime/'

foundation_gitkeep_matches=$(find KnowledgeHub -name .gitkeep -print)
[ -z "$foundation_gitkeep_matches" ] || foundation_fail "Vault filler .gitkeep files found: $foundation_gitkeep_matches"

foundation_structural_marker_allowlist='
KnowledgeHub/00_Inbox/Captures/.knowledgeos-directory
KnowledgeHub/01_AI_Review/Conflict/.knowledgeos-directory
KnowledgeHub/01_AI_Review/Expired/.knowledgeos-directory
KnowledgeHub/01_AI_Review/Pending/.knowledgeos-directory
KnowledgeHub/01_AI_Review/Rejected/.knowledgeos-directory
KnowledgeHub/01_AI_Review/Resolved/.knowledgeos-directory
KnowledgeHub/10_Journal/Daily/.knowledgeos-directory
KnowledgeHub/10_Journal/Monthly/.knowledgeos-directory
KnowledgeHub/10_Journal/Weekly/.knowledgeos-directory
KnowledgeHub/20_Projects/.knowledgeos-directory
KnowledgeHub/30_Areas/.knowledgeos-directory
KnowledgeHub/40_Knowledge/Ideas/.knowledgeos-directory
KnowledgeHub/40_Knowledge/Notes/.knowledgeos-directory
KnowledgeHub/40_Knowledge/People/.knowledgeos-directory
KnowledgeHub/40_Knowledge/Questions/.knowledgeos-directory
KnowledgeHub/40_Knowledge/Sources/.knowledgeos-directory
KnowledgeHub/50_Maps/.knowledgeos-directory
KnowledgeHub/60_Meetings/.knowledgeos-directory
KnowledgeHub/80_Assets/Audio/.knowledgeos-directory
KnowledgeHub/80_Assets/Documents/.knowledgeos-directory
KnowledgeHub/80_Assets/Images/.knowledgeos-directory
KnowledgeHub/80_Assets/Inbox/.knowledgeos-directory
KnowledgeHub/90_Archive/Captures/.knowledgeos-directory
KnowledgeHub/90_Archive/Other/.knowledgeos-directory
KnowledgeHub/90_Archive/Projects/.knowledgeos-directory
'
foundation_structural_marker_matches=$(find KnowledgeHub -name .knowledgeos-directory -print)
for foundation_marker in $foundation_structural_marker_matches; do
    printf '%s\n' "$foundation_structural_marker_allowlist" | grep -Fqx "$foundation_marker" \
        || foundation_fail "structural marker is outside the canonical allowlist: $foundation_marker"
    foundation_marker_first_line=$(sed -n '1p' "$foundation_marker")
    [ "$foundation_marker_first_line" = 'KnowledgeOS canonical directory marker; Git has no empty-directory entries.' ] \
        || foundation_fail "structural marker has unexpected bytes: $foundation_marker"
    foundation_marker_line_count=$(awk 'END {print NR}' "$foundation_marker")
    [ "$foundation_marker_line_count" = 1 ] || foundation_fail "structural marker must contain one line: $foundation_marker"
done

for foundation_pattern in \
    '.obsidian-mac/*' \
    '.obsidian-phone/*' \
    '.obsidian-tablet/*' \
    '.obsidian-mac/plugins/' \
    '.obsidian-phone/plugins/' \
    '.obsidian-tablet/plugins/'
do
    grep -Fqx "$foundation_pattern" KnowledgeHub/.gitignore || foundation_fail "Vault profile deny rule missing: $foundation_pattern"
done

foundation_bad_runtime_files=$(find runtime -type f ! -perm 600 -print)
[ -z "$foundation_bad_runtime_files" ] || foundation_fail "runtime files must have mode 0600: $foundation_bad_runtime_files"

foundation_manifest_expected='e324f9354feca17cd022d3dfaab46c18e73ce41020fee2f7b7f959ac1856651f'
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

foundation_required_vault_files=$(ruby -ryaml -e '
  blueprint = YAML.safe_load(File.read("blueprint/blueprint.yaml"), aliases: true)
  Array(blueprint.fetch("fixed_paths").fetch("required_vault_files")).each { |path| puts path }
')
for foundation_relative in $foundation_required_vault_files; do
    [ ! -L "KnowledgeHub/$foundation_relative" ] || foundation_fail "required Vault file is a symlink: $foundation_relative"
    [ -f "KnowledgeHub/$foundation_relative" ] || foundation_fail "missing required Vault file: $foundation_relative"
done

foundation_literal_matches=$(find KnowledgeHub ops runtime -type d \( \
    -name YYYY -o -name MM -o -name GGGG -o -name WWW -o \
    -name PROJECT_NAME -o -name JOB_ID \
\) -print)
[ -z "$foundation_literal_matches" ] || foundation_fail "literal placeholder directories found: $foundation_literal_matches"

foundation_symlink_matches=$(find KnowledgeHub ops runtime -type l -print)
[ -z "$foundation_symlink_matches" ] || foundation_fail "unexpected symlinks found: $foundation_symlink_matches"

foundation_control_git=$(git -C "$foundation_root" rev-parse --show-toplevel 2>/dev/null || true)
foundation_vault_git=$(git -C "$foundation_root/KnowledgeHub" rev-parse --show-toplevel 2>/dev/null || true)

if [ -z "$foundation_control_git" ] && [ -z "$foundation_vault_git" ]; then
    printf '%s\n' 'Git boundary: DEFERRED (neither repository has been initialized)'
elif [ -n "$foundation_control_git" ] && [ -n "$foundation_vault_git" ]; then
    [ "$foundation_control_git" = "$foundation_root" ] || foundation_fail "wrong control Git root: $foundation_control_git"
    [ "$foundation_vault_git" = "$foundation_root/KnowledgeHub" ] || foundation_fail "wrong Vault Git root: $foundation_vault_git"
    git -C "$foundation_root" check-ignore --no-index --quiet -- KnowledgeHub || foundation_fail 'control repository must ignore KnowledgeHub/'
    git -C "$foundation_root" check-ignore --no-index --quiet -- runtime || foundation_fail 'control repository must ignore runtime/'
    foundation_tracked_boundaries=$(git -C "$foundation_root" ls-files --stage -- KnowledgeHub runtime)
    [ -z "$foundation_tracked_boundaries" ] || foundation_fail "control index tracks forbidden boundaries: $foundation_tracked_boundaries"
    printf '%s\n' 'Git boundary: PASS (independent control and Vault roots)'
else
    foundation_fail 'only one of the two required Git repositories is initialized'
fi

printf '%s\n' 'Foundation checksum/path checks: PASS'
printf '%s\n' 'Blueprint JSON Schema validation: AVAILABLE via make blueprint-check'
printf '%s\n' 'Cross-document semantic validation: AVAILABLE via make blueprint-check (C03-C04 gates)'
printf '%s\n' 'Generated artifact zero-diff validation: AVAILABLE via make schema-check (C05-C06)'
