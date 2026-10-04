"""Preserve public names while allowing the adopted v3/v2 contracts to evolve."""

from vaultops.cli import build_parser


def test_existing_interface_names_and_new_owner_observation_commands():
    parser = build_parser()
    assert parser.parse_args(["ai", "normalize", "--source", "note.md", "--expected-sha256", "a" * 64]).ai_command == "normalize"
    for action in ("check", "status", "recover"):
        args = parser.parse_args(["operation", action])
        assert args.command == "operation" and args.operation_command == action


def test_operator_approval_and_explicit_apply_remain_separate_cli_commands():
    parser = build_parser()
    assert parser.parse_args(["ai", "approve", "--proposal", "item.md", "--sha256", "a" * 64]).ai_command == "approve"
    assert parser.parse_args(["ai", "apply", "--proposal", "item.md"]).ai_command == "apply"
