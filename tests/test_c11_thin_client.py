"""C11 Thin Client acceptance: runs the plugin's node:test suite against a stubbed Obsidian."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "thin-client" / "tests" / "thin-client.test.mjs"


def run_node(name_pattern: str | None = None) -> subprocess.CompletedProcess[str]:
    command = ["node", "--test", "--test-reporter=tap"]
    if name_pattern:
        command += ["--test-name-pattern", name_pattern]
    command.append(str(SUITE))
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)


def assert_passed(result: subprocess.CompletedProcess[str], expected: int | None = None) -> None:
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "# fail 0" in result.stdout, output
    if expected is not None:
        assert f"# pass {expected}" in result.stdout, output


def test_SUR_1_thin_client_requests_carry_path_and_digest_only():
    assert_passed(run_node("test_SUR_1_thin_client_requests_carry_path_and_digest_only"), expected=1)


def test_SUR_2_thin_client_writes_no_settings():
    assert_passed(run_node("test_SUR_2_thin_client_writes_no_settings"), expected=1)


def test_thin_client_node_suite():
    assert_passed(run_node())


def test_SUR_8_sidebar_preserves_note_context_and_existing_action_boundaries():
    assert_passed(run_node("^sidebar (keeps|inline inputs)"), expected=2)


def test_SUR_9_sidebar_inline_results_and_preview_decisions():
    assert_passed(run_node("^sidebar (renders|preview)"), expected=2)
